# -*- coding: utf-8 -*-
"""
삼삼엠투 옵션별 공실/예약률 분석 뷰어 (Flask).

임대인 질문: "이 옵션(예: TV) 없어도 잘 나갈까?"
  → 지역/건물유형으로 거른 삼삼 매물에서, 옵션 있는 집 vs 없는 집의 평균 예약률·공실률을 비교한다.
  옵션별 '있을때−없을때 예약률 차이'가 작으면 그 옵션 없어도 수요에 큰 차이가 없다는 신호.

데이터: samsam_listings (Supabase). DB가 없으면 lab/samsam_sample.jsonl(합성 프리뷰)로 폴백.
  python web/samsam_app.py     # http://127.0.0.1:5003

예약률/공실률(최근 1달, 30일 창):
  가용일 = 30 − 막힘일(blocked_days_1m)
  예약률 = booked_days_1m / 가용일,  공실률 = 1 − 예약률
"""
import hmac
import json
import os
import statistics
import sys
from datetime import datetime

import requests
from flask import Flask, jsonify, render_template, request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 윈도우 콘솔(cp949)에서 로그의 em-dash 등 유니코드가 못 찍혀 500 나는 것 방지(리눅스는 무영향).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
SAMPLE = os.path.join(ROOT, "lab", "samsam_sample.jsonl")
# 배포(미국 함수)에서 DB(서울) 왕복을 피하려고, export된 파일이 있으면 그걸 우선 읽는다.
EXPORT = os.path.join(ROOT, "lab", "samsam_listings.jsonl")
SNAP_EXPORT = os.path.join(ROOT, "lab", "samsam_snapshots.jsonl")

app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"))
from auth import current_user, init_auth  # noqa: E402
init_auth(app)

# 삼삼 통합 채팅: 계정 연결(Playwright 1회 로그인) + 폴링 결과 조회.
sys.path.insert(0, os.path.join(ROOT, "pipeline", "samsam"))
import chat_auth  # noqa: E402
import chat_poll  # noqa: E402
import crypto_util  # noqa: E402
import db  # noqa: E402

SAM_COLS = ("room_id", "url", "name", "building_type", "building_name",
            "sido", "sigungu", "dong", "area_pyeong", "rent_total_weekly",
            "booked_days_1m", "blocked_days_1m", "basic_options", "extra_options",
            "station_500m_names", "collected_at")

# 삼삼 옵션 영문 코드 → 한글 표시명
OPTION_KO = {
    "REFRIGERATOR": "냉장고", "WASHING_MACHINE": "세탁기", "AIR_CONDITIONER": "에어컨",
    "TV": "TV", "WIFI": "와이파이", "SINK": "싱크대", "GAS_STOVE": "가스레인지",
    "INDUCTION": "인덕션", "BED": "침대", "DESK": "책상", "CLOSET": "옷장",
    "SHOE_RACK": "신발장", "DOOR_LOCK": "도어락", "CCTV": "CCTV",
    "MANAGEMENT_OFFICE": "관리실", "DINING_TABLE": "식탁", "MICROWAVE": "전자레인지",
    "RICE_COOKER": "밥솥", "SOFA": "소파", "WATER_PURIFIER": "정수기", "VANITY": "화장대",
    "BATHTUB": "욕조", "DRYER": "건조기", "BALCONY": "발코니", "DRESSING_ROOM": "드레스룸",
    "AIR_PURIFIER": "공기청정기", "GAS_RANGE": "가스레인지", "ELECTRIC_RANGE": "전기레인지",
    "CURTAINS": "커튼", "CABLE_TV": "케이블TV", "BIDET": "비데",
}


def ko(code):
    return OPTION_KO.get(code, code)


# 판정 임계값: '없는 집'·보정표본이 이만큼은 돼야 보정차이를 신뢰. 보유율 95%+는 필수재로 분류.
MIN_NONE = 20
MIN_ADJ = 15
ESSENTIAL_ADOPTION = 95


def _parse_list(v):
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.strip().startswith("["):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return []
    return []


def _enrich(r):
    """행에 options(set)·occ·vac·주당만원·역 파생."""
    opts = set(_parse_list(r.get("basic_options")) + _parse_list(r.get("extra_options")))
    r["options"] = opts
    blocked = r.get("blocked_days_1m") or 0
    booked = r.get("booked_days_1m") or 0
    avail = max(31 - blocked, 1)   # 수집 윈도우 오늘~+30일=31일(양끝 포함)
    r["occ"] = min(1.0, booked / avail)        # 예약률
    r["vac"] = 1 - r["occ"]                     # 공실률
    r["sam_week_man"] = round((r.get("rent_total_weekly") or 0) / 10000, 1)
    st = _parse_list(r.get("station_500m_names"))
    r["stations"] = st
    r["station"] = st[0] if st else ""
    return r


def _load_db():
    try:
        import db
        conn = db.connect()
        rows = [dict(x) for x in conn.execute(
            f"SELECT {', '.join(SAM_COLS)} FROM samsam_listings"
        ).fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"[samsam_app] DB 미연결({type(e).__name__}) → 샘플 폴백", flush=True)
        return None


def _load_sample():
    rows = []
    if not os.path.exists(SAMPLE):
        return rows
    with open(SAMPLE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_listings():
    # 1순위: export 파일(빠름, DB 왕복 없음) → 2순위: DB → 3순위: 합성 샘플
    if os.path.exists(EXPORT):
        rows, src = _load_jsonl(EXPORT), f"파일({os.path.basename(EXPORT)})"
    else:
        rows = _load_db()
        src = "DB(samsam_listings)"
        if not rows:
            rows = _load_sample()
            src = f"샘플({os.path.basename(SAMPLE)}, 합성 프리뷰)"
    rows = [_enrich(r) for r in rows]
    print(f"[samsam_app] {len(rows)}건 로드 — 출처: {src}", flush=True)
    return rows, src


_LC=None
def _ensure():
    global _LC
    if _LC is None:
        _LC=load_listings()
    return _LC
def L():
    return _ensure()[0]
def SRC():
    return _ensure()[1]


# ── 네이버 매칭 결과(net_profit_integrated.csv) → room_id별 수익 정보 ──
MATCH_CSV = os.path.join(ROOT, "data", "net_profit_integrated.csv")
CONV_PER_MONTH = 0.06 / 12   # 전월세 전환율(월). 보증금 D → 월세환산 = 환산월세 − D×CONV


def _load_matches():
    import csv
    out = {}
    if not os.path.exists(MATCH_CSV):
        return out

    def num(v):
        v = (v or "").replace(",", "")
        try:
            return float(v)
        except ValueError:
            return None
    with open(MATCH_CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                rid = int(r["삼삼ID"])
            except (KeyError, ValueError):
                continue
            out[rid] = {
                "maxRev": num(r.get("삼삼월환산_만원")),
                "nRent": num(r.get("네이버월세_만원")),
                "nMgmt": num(r.get("네이버관리비_만원")),
                "nDep": num(r.get("네이버보증금_만원")),
                "nEquiv": num(r.get("네이버환산월세_만원")),
            }
    return out


_MC=None
def M():
    global _MC
    if _MC is None:
        _MC=_load_matches()
    return _MC


def calc_at_deposit(rid, dep, fixed=0.0):
    """보증금 dep(만원) 기준 분해값 dict 반환(매칭 없으면 None).
    월순수익 = 삼삼월수익(최대) − 네이버월세@dep − 관리비 − 고정비(통신비·청소비 등).
    네이버월세@dep = 환산월세 − dep×전환율/12 (보증금 정규화 역산)."""
    m = M().get(rid)
    if not m or m.get("maxRev") is None or m.get("nEquiv") is None:
        return None
    rent = round(max(0.0, m["nEquiv"] - dep * CONV_PER_MONTH), 1)
    mgmt = m.get("nMgmt") or 0
    net = round(m["maxRev"] - rent - mgmt - fixed, 1)
    return {"maxRev": m["maxRev"], "rent": rent, "mgmt": mgmt, "dep": dep,
            "fixed": fixed, "net": net}


def net_at_deposit(rid, dep, fixed=0.0):
    c = calc_at_deposit(rid, dep, fixed)
    return c["net"] if c else None


def _filtered(a):
    rows = L()
    for key in ("sido", "sigungu", "dong", "building_type"):
        v = a.get(key)
        if v:
            rows = [r for r in rows if r.get(key) == v]

    def rng(field, lo, hi, scale=1.0):
        nonlocal rows
        if a.get(lo):
            v = float(a[lo]); rows = [r for r in rows if (r.get(field) or 0) / scale >= v]
        if a.get(hi):
            v = float(a[hi]); rows = [r for r in rows if (r.get(field) or 0) / scale <= v]

    rng("area_pyeong", "pyeong_min", "pyeong_max")
    rng("rent_total_weekly", "week_min", "week_max", scale=10000)   # 만원 기준
    return rows


def _grp(rows):
    n = len(rows)
    if not n:
        return {"n": 0, "occ": None, "vac": None, "week": None, "pyeong": None}
    return {
        "n": n,
        "occ": round(statistics.mean(r["occ"] for r in rows) * 100, 1),
        "vac": round(statistics.mean(r["vac"] for r in rows) * 100, 1),
        "week": round(statistics.mean(r["sam_week_man"] for r in rows), 1),
        "pyeong": round(statistics.mean((r.get("area_pyeong") or 0) for r in rows), 1),
    }


def _pbucket(r):
    return int((r.get("area_pyeong") or 0) // 2)        # 2평 단위


def _wbucket(r):
    return int((r.get("sam_week_man") or 0) // 10)      # 주당 10만원 단위


def _adj_diff(rows, opt):
    """같은 평수대(2평)·같은 가격대(주당 10만) 칸 안에서만 옵션 유무 예약률 차이를 비교(가중평균).

    교란(옵션 없는 집이 우연히 더 크거나 비싼 경우)을 제거한 '보정 차이'(%p)와 표본수 반환.
    """
    from collections import defaultdict
    buckets = defaultdict(list)
    for r in rows:
        buckets[(_pbucket(r), _wbucket(r))].append(r)
    num = den = 0
    for rs in buckets.values():
        have = [x for x in rs if opt in x["options"]]
        none = [x for x in rs if opt not in x["options"]]
        if have and none:
            d = statistics.mean(x["occ"] for x in have) - statistics.mean(x["occ"] for x in none)
            w = min(len(have), len(none))   # 두 그룹 중 작은 쪽을 가중치로
            num += d * w
            den += w
    return (round(num / den * 100, 1), den) if den else (None, 0)


@app.route("/")
def index():
    return render_template("samsam.html")


@app.route("/api/facets")
def api_facets():
    sidos = sorted({r["sido"] for r in L() if r.get("sido")})
    tree = {}
    for r in L():
        tree.setdefault(r.get("sido", ""), {}).setdefault(r.get("sigungu", ""), set()).add(r.get("dong", ""))
    tree = {s: {g: sorted(d) for g, d in gg.items()} for s, gg in tree.items()}
    btypes = sorted({r["building_type"] for r in L() if r.get("building_type")})
    opts = [{"code": c, "name": ko(c)} for c in sorted({o for r in L() for o in r["options"]})]
    return jsonify({"sido": sidos, "tree": tree, "building_type": btypes,
                    "options": opts, "total": len(L()), "source": SRC(),
                    "occ_window": _occ_window()})


def _occ_window():
    """예약률 산정 구간 안내: 예약률 = 수집일 ~ +30일의 예약 비율."""
    import datetime as _dt
    dates = sorted({(r.get("collected_at") or "")[:10] for r in L() if r.get("collected_at")})
    if not dates:
        return "예약률 = 수집일 기준 향후 30일 (수집일 정보 없음)"
    lo, hi = dates[0], dates[-1]
    try:
        end = (_dt.date.fromisoformat(hi) + _dt.timedelta(days=30)).isoformat()
    except ValueError:
        end = "+30일"
    span = lo if lo == hi else f"{lo}~{hi}"
    return f"예약률 기준: 수집일({span}) ~ 향후 30일(~{end})의 예약 비율"


@app.route("/api/analyze")
def api_analyze():
    """지역/유형으로 거른 집합의 전체 통계 + 옵션별 있음/없음 예약률 비교 표."""
    rows = _filtered(request.args)
    overall = _grp(rows)
    total = len(rows)
    opts = sorted({o for r in rows for o in r["options"]})
    table = []
    for o in opts:
        have = [r for r in rows if o in r["options"]]
        none = [r for r in rows if o not in r["options"]]
        gh, gn = _grp(have), _grp(none)
        diff = (round(gh["occ"] - gn["occ"], 1)
                if gh["occ"] is not None and gn["occ"] is not None else None)
        adj, adjn = _adj_diff(rows, o)   # 같은 평수·가격대 보정 차이
        adoption = round(len(have) / total * 100, 1) if total else 0
        # 판정: 보유율 95%+ = 필수재(거의 다 보유 → 효과 측정 불가). 그 외엔 없는집/보정표본이
        # 충분해야 측정 가능. 둘 다 아니면 표본부족.
        if adoption >= ESSENTIAL_ADOPTION:
            verdict = "essential"      # 사실상 필수
        elif len(none) >= MIN_NONE and adjn >= MIN_ADJ:
            verdict = "measurable"     # 측정 가능(보정차이 신뢰)
        else:
            verdict = "lowsample"      # 표본부족
        table.append({"option": o, "name": ko(o), "have": gh, "none": gn,
                      "diff": diff, "adj": adj, "adjn": adjn,
                      "adoption": adoption, "verdict": verdict})

    def _key(x):
        # 측정가능(보정차이 큰 순) → 필수재(보유율 순) → 표본부족
        rank = {"measurable": 0, "essential": 1, "lowsample": 2}[x["verdict"]]
        second = -(x["adj"] or 0) if x["verdict"] == "measurable" else -x["adoption"]
        return (rank, second)
    table.sort(key=_key)
    return jsonify({"overall": overall, "table": table})


@app.route("/api/listings")
def api_listings():
    """지역/유형 + 특정 옵션 유무로 거른 실제 매물 목록."""
    a = request.args
    rows = _filtered(a)
    option = a.get("option", "")
    mode = a.get("mode", "none")   # none=옵션 없는 집, have=있는 집
    if option:
        if mode == "have":
            rows = [r for r in rows if option in r["options"]]
        else:
            rows = [r for r in rows if option not in r["options"]]
    rows = sorted(rows, key=lambda r: r["vac"], reverse=True)   # 공실 높은 순
    items = [{
        "room_id": r["room_id"], "name": r.get("name", ""),
        "building_name": r.get("building_name") or "",
        "building_type": r.get("building_type", ""),
        "sigungu": r.get("sigungu", ""), "dong": r.get("dong", ""),
        "station": r.get("station", ""), "pyeong": r.get("area_pyeong"),
        "week": r["sam_week_man"], "booked": r.get("booked_days_1m"),
        "blocked": r.get("blocked_days_1m"),
        "occ": round(r["occ"] * 100, 1), "vac": round(r["vac"] * 100, 1),
        "options": [ko(o) for o in sorted(r["options"])], "url": r.get("url", ""),
    } for r in rows]
    return jsonify({"total": len(items), "items": items, "optionName": ko(option)})


@app.route("/api/buildings")
def api_buildings():
    """건물(오피스텔) 단위 인기 순위 — 한 건물에 삼삼 매물이 여러 채 있고 그게 다 잘 나가면
    '검증된 대박 건물'. 매물수(n)·평균예약률·최저예약률(전 호실 다 잘 나가는지)·평균주당."""
    a = request.args
    rows = _filtered(a)
    st = a.get("station", "").strip()
    if st:   # 역 검색: 매물 500m 내 역명에 검색어 포함
        rows = [r for r in rows if any(st in s for s in r.get("stations", []))]
    try:
        min_n = max(1, int(a.get("min_n", 2)))
    except ValueError:
        min_n = 2
    try:
        dep = float(a.get("deposit", 1000) or 1000)   # 보증금 기준(만원), 기본 1000
    except ValueError:
        dep = 1000
    try:
        fixed = float(a.get("fixed", 0) or 0)          # 고정비(통신비·청소비 등, 만원/월)
    except ValueError:
        fixed = 0.0
    by = {}
    for r in rows:
        bn = (r.get("building_name") or "").strip()
        if not bn:
            continue
        # 같은 건물이라도 평수가 다르면 따로 묶는다(평수 섞어 평균내면 수익이 왜곡됨).
        py = r.get("area_pyeong")
        by.setdefault((r.get("sigungu", ""), r.get("dong", ""), bn, py), []).append(r)
    out = []
    for (sg, dong, bn, py), xs in by.items():
        if len(xs) < min_n:
            continue
        occs = [x["occ"] * 100 for x in xs]
        calcs = [calc_at_deposit(x["room_id"], dep, fixed) for x in xs]
        calcs = [c for c in calcs if c is not None]
        avg = (lambda key: round(statistics.mean(c[key] for c in calcs), 1)) if calcs else (lambda key: None)
        out.append({
            "building": bn, "sigungu": sg, "dong": dong,
            "btype": xs[0].get("building_type", ""),
            "pyeong": py,
            "n": len(xs),
            "occ_avg": round(statistics.mean(occs), 1),
            "occ_min": round(min(occs), 1),
            "occ_max": round(max(occs), 1),
            "week_avg": round(statistics.mean(x["sam_week_man"] for x in xs), 1),
            "n_matched": len(calcs),
            "net_avg": avg("net"),
            # 월순수익 분해(보증금 기준 평균): 삼삼매출 − 네이버월세 − 관리비 − 고정비
            "bd": {"maxRev": avg("maxRev"), "rent": avg("rent"), "mgmt": avg("mgmt"),
                   "dep": dep, "fixed": fixed} if calcs else None,
            "station": next((x["station"] for x in xs if x.get("station")), ""),
            "room_ids": [x["room_id"] for x in xs],
        })
    # 평균예약률 높고 매물 많은 순. (최저예약률도 높으면 전 호실 검증된 건물)
    out.sort(key=lambda r: (-r["occ_avg"], -r["n"]))
    return jsonify({"total": len(out), "items": out})


@app.route("/api/trend")
def api_trend():
    """주간 스냅샷(samsam_snapshots)으로 지역(동)별 예약률 추이 + 전주대비 변화(Δ)."""
    a = request.args
    rows = []
    sido_f, sigungu_f = a.get("sido"), a.get("sigungu")
    try:
        if os.path.exists(SNAP_EXPORT):   # 파일 우선(DB 왕복 없음)
            for r in _load_jsonl(SNAP_EXPORT):
                if sido_f and r.get("sido") != sido_f:
                    continue
                if sigungu_f and r.get("sigungu") != sigungu_f:
                    continue
                rows.append(r)
        else:
            import db
            conn = db.connect()
            where, params = [], []
            if sido_f:
                where.append("sido=%s"); params.append(sido_f)
            if sigungu_f:
                where.append("sigungu=%s"); params.append(sigungu_f)
            w = (" WHERE " + " AND ".join(where)) if where else ""
            rows = [dict(r) for r in conn.execute(
                "SELECT snapshot_date, sido, sigungu, dong, n, avg_occ_1m"
                f" FROM samsam_snapshots{w}", params).fetchall()]
            conn.close()
    except Exception as e:
        return jsonify({"dates": [], "items": [], "error": str(e)[:80]})

    dates = sorted({r["snapshot_date"] for r in rows})
    agg = {}   # (sigungu,dong) -> {date: [sum n*occ, sum n]}
    for r in rows:
        key = (r["sigungu"] or "", r["dong"] or "")
        cell = agg.setdefault(key, {}).setdefault(r["snapshot_date"], [0.0, 0])
        cell[0] += (r["avg_occ_1m"] or 0) * (r["n"] or 0)
        cell[1] += r["n"] or 0

    out = []
    for (sg, dong), dd in agg.items():
        series = {d: (round(v[0] / v[1], 1) if v[1] else None) for d, v in dd.items()}
        latest = series.get(dates[-1]) if dates else None
        prev = series.get(dates[-2]) if len(dates) >= 2 else None
        delta = round(latest - prev, 1) if (latest is not None and prev is not None) else None
        n_latest = dd.get(dates[-1], [0, 0])[1] if dates else 0
        out.append({"sigungu": sg, "dong": dong, "series": series,
                    "latest": latest, "delta": delta, "n": n_latest})
    out.sort(key=lambda r: (r["latest"] is None, -(r["latest"] or 0)))
    return jsonify({"dates": dates, "items": out})


GH_REPO = "gunho30811/STA"
GH_CHAT_POLL_WORKFLOW = "samsam-chat-poll.yml"


def _trigger_chat_poll_workflow():
    """계정 연결 직후 GH Actions 폴링 workflow(Playwright 있는 환경)를 즉시 트리거해
    10분 스케줄을 기다리지 않고 곧바로 로그인을 처리하게 한다. 실패해도 조용히 무시 —
    GH_DISPATCH_TOKEN 미설정이거나 API 호출이 실패해도 기존 10분 스케줄이 안전망으로 남는다."""
    token = os.environ.get("GH_DISPATCH_TOKEN")
    if not token:
        return
    try:
        requests.post(
            f"https://api.github.com/repos/{GH_REPO}/actions/workflows/"
            f"{GH_CHAT_POLL_WORKFLOW}/dispatches",
            json={"ref": "main"},
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json"},
            timeout=8)
    except Exception:
        pass


@app.route("/chat/")
def chat_page():
    return render_template("samsam_chat.html")


@app.route("/chat/api/accounts")
def chat_api_accounts():
    u = current_user()
    conn = db.connect()
    rows = conn.execute(
        "SELECT id, samsam_email, label, status, last_error, last_polled_at "
        "FROM samsam_accounts WHERE member_id=%s ORDER BY id", (u["id"],)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/chat/api/accounts", methods=["POST"])
def chat_api_add_account():
    u = current_user()
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    label = (data.get("label") or "").strip()
    if not email or not password:
        return jsonify({"error": "이메일/비밀번호를 입력해주세요."}), 400
    try:
        res = chat_auth.login_and_get_refresh_token(email, password)
    except chat_auth.LoginError as e:
        return jsonify({"error": str(e)}), 400
    except ModuleNotFoundError:
        # Vercel 등 서버리스 배포엔 Playwright(브라우저 자동화)가 없어 이 요청 안에서
        # 로그인을 못 끝낸다. 비번만 암호화해 큐잉해두면 GH Actions 폴링 workflow가
        # (Playwright 설치된 환경) 다음 주기에 로그인을 대신 완료한다.
        conn = db.connect()
        conn.execute(
            "INSERT INTO samsam_accounts (member_id, samsam_email, label, password_enc, "
            "status, created_at) VALUES (%s,%s,%s,%s,'pending_login',%s)",
            (u["id"], email, label or email, crypto_util.encrypt(password),
             datetime.now().isoformat(timespec="seconds")))
        conn.commit()
        conn.close()
        _trigger_chat_poll_workflow()
        return jsonify({"ok": True, "pending": True,
                         "message": "로그인 처리 중입니다. 잠시 후(보통 1분 이내) 새로고침해주세요."})
    except Exception as e:
        return jsonify({"error": f"로그인 중 오류: {repr(e)[:120]}"}), 500

    conn = db.connect()
    conn.execute(
        "INSERT INTO samsam_accounts (member_id, samsam_email, label, password_enc, "
        "refresh_token_enc, samsam_member_id, status, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,'ok',%s)",
        (u["id"], email, label or email, crypto_util.encrypt(password),
         crypto_util.encrypt(res["refresh_token"]), res["samsam_member_id"],
         datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/chat/api/accounts/<int:acct_id>", methods=["DELETE"])
def chat_api_delete_account(acct_id):
    u = current_user()
    conn = db.connect()
    conn.execute("DELETE FROM samsam_accounts WHERE id=%s AND member_id=%s", (acct_id, u["id"]))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/chat/api/poll", methods=["POST"])
def chat_api_poll():
    """이 회원이 연결한 계정만 지금 즉시 폴링(로컬 테스트/수동 새로고침용)."""
    u = current_user()
    conn = db.connect()
    accounts = conn.execute(
        "SELECT id, member_id, samsam_email, label, password_enc, refresh_token_enc, "
        "samsam_member_id FROM samsam_accounts WHERE member_id=%s AND status != 'disabled'",
        (u["id"],)).fetchall()
    for acct in accounts:
        chat_poll.poll_account(conn, dict(acct))
    conn.close()
    return jsonify({"ok": True, "polled": len(accounts)})


@app.route("/chat/api/cron-poll", methods=["GET", "POST"])
def chat_api_cron_poll():
    """외부 무료 크론 서비스(예: cron-job.org)가 1분마다 호출 — 전체 계정 폴링.

    순수 HTTP(토큰 갱신 + RTDB 조회)라 Vercel에서도 바로 돌아간다. 로그인/재로그인은
    Playwright가 필요해 여기선 항상 실패(reauth_needed로 표시)하고, 그건 GH Actions
    쪽 10분 스케줄(samsam-chat-poll.yml)이 대신 처리한다.
    """
    secret = os.environ.get("CRON_SECRET")
    key = request.args.get("key", "")
    if not secret or not hmac.compare_digest(key, secret):
        return jsonify({"error": "unauthorized"}), 403
    conn = db.connect()
    n = chat_poll.poll_all(conn)
    conn.close()
    return jsonify({"ok": True, "polled": n})


@app.route("/chat/api/rooms")
def chat_api_rooms():
    u = current_user()
    conn = db.connect()
    rows = conn.execute(
        "SELECT r.id, r.room_name, r.counterpart_nickname, r.last_message, r.last_message_time, "
        "r.last_read_at, r.chat_room_status, r.contract_status, a.label, a.samsam_email "
        "FROM samsam_chat_rooms r JOIN samsam_accounts a ON a.id = r.account_id "
        "WHERE a.member_id=%s AND r.host_or_guest='host' "
        "ORDER BY r.last_message_time DESC NULLS LAST", (u["id"],)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["unread"] = bool(d["last_message_time"]
                            and (not d["last_read_at"] or d["last_message_time"] > d["last_read_at"]))
        out.append(d)
    return jsonify(out)


@app.route("/chat/api/rooms/<int:room_id>/messages")
def chat_api_messages(room_id):
    u = current_user()
    conn = db.connect()
    owner = conn.execute(
        "SELECT a.samsam_member_id AS owner_id, r.last_message_time "
        "FROM samsam_chat_rooms r JOIN samsam_accounts a ON a.id = r.account_id "
        "WHERE r.id=%s AND a.member_id=%s", (room_id, u["id"])).fetchone()
    if not owner:
        conn.close()
        return jsonify({"error": "not found"}), 404
    rows = conn.execute(
        "SELECT msg_key, sender, receiver, message, message_type, message_time, title "
        "FROM samsam_chat_messages WHERE room_id=%s ORDER BY message_time ASC", (room_id,)).fetchall()
    pending = conn.execute(
        "SELECT id, message, status, created_at FROM samsam_chat_outbox "
        "WHERE room_id=%s AND status='pending' ORDER BY id ASC", (room_id,)).fetchall()
    # 방을 열람했으니 지금까지의 메시지는 읽음 처리(미확인 배지 해제).
    conn.execute("UPDATE samsam_chat_rooms SET last_read_at=%s WHERE id=%s",
                 (owner["last_message_time"], room_id))
    conn.commit()
    conn.close()
    return jsonify({"owner_id": owner["owner_id"], "messages": [dict(r) for r in rows],
                    "pending": [dict(r) for r in pending]})


@app.route("/chat/api/rooms/<int:room_id>/send", methods=["POST"])
def chat_api_send_message(room_id):
    """답장 큐잉 — 삼삼 쓰기는 브라우저 UI 조작(Playwright)으로만 가능해 여기선 큐잉만 하고,
    GH Actions(samsam-chat-poll.yml)가 실제 발송을 처리한다(연결 계정 즉시 로그인과 동일 구조)."""
    u = current_user()
    data = request.get_json(force=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "메시지를 입력해주세요."}), 400
    conn = db.connect()
    owner = conn.execute(
        "SELECT r.id FROM samsam_chat_rooms r JOIN samsam_accounts a ON a.id = r.account_id "
        "WHERE r.id=%s AND a.member_id=%s", (room_id, u["id"])).fetchone()
    if not owner:
        conn.close()
        return jsonify({"error": "not found"}), 404
    conn.execute(
        "INSERT INTO samsam_chat_outbox (room_id, message, status, created_at) "
        "VALUES (%s,%s,'pending',%s)",
        (room_id, message, datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    conn.close()
    _trigger_chat_poll_workflow()
    return jsonify({"ok": True})


if __name__ == "__main__":
    import socket
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        ip = "127.0.0.1"
    print(f"출처: {SRC()} / {len(L())}건")
    print("로컬:   http://127.0.0.1:5003")
    print(f"같은망: http://{ip}:5003")
    app.run(host="0.0.0.0", port=5003, debug=False)
