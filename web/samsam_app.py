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
import json
import os
import statistics
import sys

from flask import Flask, jsonify, render_template, request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SAMPLE = os.path.join(ROOT, "lab", "samsam_sample.jsonl")
# 배포(미국 함수)에서 DB(서울) 왕복을 피하려고, export된 파일이 있으면 그걸 우선 읽는다.
EXPORT = os.path.join(ROOT, "lab", "samsam_listings.jsonl")
SNAP_EXPORT = os.path.join(ROOT, "lab", "samsam_snapshots.jsonl")

app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"))
from auth import init_auth  # noqa: E402
init_auth(app)

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
