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
from flask import Flask, jsonify, request, send_from_directory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DIST = os.path.join(ROOT, "frontend", "dist", "samsam")   # React(Vite) 빌드 산출물(메인 뷰어)

# 윈도우 콘솔(cp949)에서 로그의 em-dash 등 유니코드가 못 찍혀 500 나는 것 방지(리눅스는 무영향).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
SAMPLE = os.path.join(ROOT, "lab", "samsam_sample.jsonl")
# 계약=DB. 예전엔 lab/samsam_listings.jsonl(크롤 export)를 파일 우선으로 읽었지만,
# 크롤/웹 분리로 파일 계약을 없애고 samsam_listings/samsam_snapshots 를 DB에서 직접 읽는다.
# (DB 불가 시 합성 샘플로만 폴백.)

app = Flask(__name__)
from auth import current_user, init_auth  # noqa: E402
init_auth(app)

# 삼삼 통합 채팅: 계정 연결(Playwright 1회 로그인) + 폴링 결과 조회.
sys.path.insert(0, os.path.join(ROOT, "pipeline", "samsam"))
import chat_auth  # noqa: E402
import chat_poll  # noqa: E402
import crypto_util  # noqa: E402
import db  # noqa: E402
import search  # noqa: E402  (건물명·역명 텍스트 검색 색인 — 외부 엔진 없이 순수 파이썬 n-gram)

SAM_COLS = ("room_id", "url", "name", "building_type", "building_name",
            "sido", "sigungu", "dong", "area_pyeong", "rent_total_weekly",
            "booked_days_1m", "booked_days_2m", "booked_days_3m", "blocked_days_1m",
            "basic_options", "extra_options", "station_500m_names", "collected_at")

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
    r["occ"] = min(1.0, booked / avail)        # 예약률(1달)
    r["vac"] = 1 - r["occ"]                     # 공실률(1달)
    # 2·3달 예약률: 누적 예약일(booked_days_2m=0~60일, 3m=0~90일)을 창 길이로 나눔.
    # 2·3달치 blocked는 수집을 안 해(1m만) 보정 없이 계산 — snapshot.py의 avg_occ_3m과 동일 관례.
    r["occ2"] = min(1.0, (r.get("booked_days_2m") or 0) / 61)
    r["occ3"] = min(1.0, (r.get("booked_days_3m") or 0) / 91)
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


def load_listings():
    # 계약=DB. samsam_listings 를 DB에서 직접 읽는다. DB 불가 시에만 합성 샘플로 폴백.
    rows = _load_db()
    src = "DB"
    if not rows:
        rows = _load_sample()
        src = "샘플(합성 프리뷰)"
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


# ── 검색 색인(건물명·역명) ──────────────────────────────────────────────────────
# 앱의 텍스트 검색은 search 패키지의 n-gram 역색인을 거친다. 데이터가 프로세스당 1회
# 로드(_LC)되므로 색인도 최초 사용 때 한 번만 구축한다. 데이터가 적어 메모리로 충분.
_IDX = None
def _indexes():
    global _IDX
    if _IDX is None:
        bi, si = search.TextIndex(), search.TextIndex()
        for r in L():
            bn = (r.get("building_name") or "").strip()
            if bn:
                bi.add(bn)
            for s in (r.get("stations") or []):
                if s:
                    si.add(s)
        _IDX = {"building": bi, "station": si}
        print(f"[samsam_app] 검색 색인 구축 — 건물명 {len(bi)} · 역명 {len(si)}", flush=True)
    return _IDX


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
                "nUrl": (r.get("네이버링크") or "").strip(),
                # 대표 네이버 매물 연락처·조건(전화 문의용). 구 CSV엔 없을 수 있어 .get으로 방어.
                "phone": (r.get("부동산번호") or "").strip(),
                "office": (r.get("중개사무소") or "").strip(),
                "repRent": num(r.get("대표월세_만원")),
                "repDep": num(r.get("대표보증금_만원")),
                "repFloor": (r.get("대표층수") or "").strip(),
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


def _city(sigungu):
    """시군구 표기를 시 단위로 정규화 — 수집 소스에 따라 같은 도시가 '부천시'(구 없이)와
    '부천시 원미구'(구 포함) 두 가지 문자열로 갈라져 필터에 중복으로 뜨는 문제를 막는다.
    '강남구'처럼 애초에 한 토큰인 서울/인천 구는 그대로 유지된다."""
    return (sigungu or "").split(" ")[0]


def _multi(a, key):
    """쿼리에서 같은 key로 온 여러 값(복수 선택)을 리스트로 — 콤마로 붙여 보낸 것도 분해.
    request.args(MultiDict)면 getlist, 일반 dict면 get 폴백."""
    getlist = getattr(a, "getlist", None)
    raw = getlist(key) if getlist else ([a.get(key)] if a.get(key) else [])
    out = []
    for v in raw:
        out.extend(p.strip() for p in str(v).split(",") if p.strip())
    return out


def _filtered(a):
    rows = L()
    for key in ("sido", "dong", "building_type"):
        vals = set(_multi(a, key))
        if vals:
            rows = [r for r in rows if r.get(key) in vals]
    sigungu = set(_multi(a, "sigungu"))
    if sigungu:
        rows = [r for r in rows if _city(r.get("sigungu")) in sigungu]

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
    # React(Vite) 빌드 서빙. index.html은 '문자열'로 반환(auth.py 네비 주입이 동작하려면 direct_passthrough 회피).
    idx = os.path.join(DIST, "index.html")
    if os.path.exists(idx):
        with open(idx, encoding="utf-8") as f:
            return f.read()
    return ("<h3>프론트엔드 빌드가 없습니다.</h3>"
            "<p><code>cd frontend &amp;&amp; npm run build:samsam</code> 후 새로고침하세요.</p>"), 200


@app.route("/assets/<path:filename>")
def assets(filename):
    return send_from_directory(os.path.join(DIST, "assets"), filename)


@app.route("/api/facets")
def api_facets():
    sidos = sorted({r["sido"] for r in L() if r.get("sido")})
    tree = {}
    for r in L():
        tree.setdefault(r.get("sido", ""), {}).setdefault(_city(r.get("sigungu")), set()).add(r.get("dong", ""))
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
        "occ2": round(r["occ2"] * 100, 1), "occ3": round(r["occ3"] * 100, 1),
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
    if st:   # 역 검색: 매물 500m 내 역명에 검색어 부분일치(search 색인)
        hits = _indexes()["station"].search(st)
        rows = [r for r in rows if hits.intersection(r.get("stations", []))]
    bq = a.get("building", "").strip()
    if bq:   # 건물명(오피스텔 명) 검색: 부분일치(search 색인)
        hits = _indexes()["building"].search(bq)
        rows = [r for r in rows if (r.get("building_name") or "").strip() in hits]
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
    try:
        occ_min_filter = float(a["occ_min_filter"]) if a.get("occ_min_filter") else None
    except ValueError:
        occ_min_filter = None
    try:
        net_min_filter = float(a["net_min_filter"]) if a.get("net_min_filter") else None
    except ValueError:
        net_min_filter = None
    try:
        be_max_filter = float(a["breakeven_max"]) if a.get("breakeven_max") else None
    except ValueError:
        be_max_filter = None
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
        occs2 = [x["occ2"] * 100 for x in xs]
        occs3 = [x["occ3"] * 100 for x in xs]
        calcs = [calc_at_deposit(x["room_id"], dep, fixed) for x in xs]
        calcs = [c for c in calcs if c is not None]
        avg = (lambda key: round(statistics.mean(c[key] for c in calcs), 1)) if calcs else (lambda key: None)
        # 링크용 대표 매물: 네이버 매칭이 있는 방을 우선(부동산링크까지 같이 나오게), 없으면 그냥 첫 방.
        sample = next((x for x in xs if M().get(x["room_id"], {}).get("nUrl")), xs[0])
        week_avg = round(statistics.mean(x["sam_week_man"] for x in xs), 1)
        # 손익분기점(주) = (네이버월세@보증금 + 관리비) ÷ 삼삼 주당매출.
        # 월 고정비용을 주당 매출로 갚는 데 걸리는 주 수 → 작을수록 회수 빠름(좋음).
        breakeven = None
        if calcs and week_avg > 0:
            monthly_cost = avg("rent") + avg("mgmt")
            breakeven = round(monthly_cost / week_avg, 1)
        out.append({
            "building": bn, "sigungu": sg, "dong": dong,
            "btype": xs[0].get("building_type", ""),
            "pyeong": py,
            "n": len(xs),
            "occ_avg": round(statistics.mean(occs), 1),
            "occ_min": round(min(occs), 1),
            "occ_max": round(max(occs), 1),
            "occ2_avg": round(statistics.mean(occs2), 1),   # 2달 평균 예약률
            "occ3_avg": round(statistics.mean(occs3), 1),   # 3달 평균 예약률
            "week_avg": week_avg,
            "n_matched": len(calcs),
            "net_avg": avg("net"),
            "breakeven": breakeven,
            # 월순수익 분해(보증금 기준 평균): 삼삼매출 − 네이버월세 − 관리비 − 고정비
            "bd": {"maxRev": avg("maxRev"), "rent": avg("rent"), "mgmt": avg("mgmt"),
                   "dep": dep, "fixed": fixed} if calcs else None,
            "station": next((x["station"] for x in xs if x.get("station")), ""),
            "room_ids": [x["room_id"] for x in xs],
            "sam_url": sample.get("url", "") or "",
            "naver_url": M().get(sample["room_id"], {}).get("nUrl", "") or "",
            # 대표(sample) 매물의 네이버 연락처·조건 — CSV에서 전화 문의용.
            "phone": M().get(sample["room_id"], {}).get("phone", "") or "",
            "office": M().get(sample["room_id"], {}).get("office", "") or "",
            "nv_rent": M().get(sample["room_id"], {}).get("repRent"),
            "nv_dep": M().get(sample["room_id"], {}).get("repDep"),
            "nv_floor": M().get(sample["room_id"], {}).get("repFloor", "") or "",
        })
    if occ_min_filter is not None:
        out = [r for r in out if r["occ_min"] >= occ_min_filter]
    if net_min_filter is not None:
        out = [r for r in out if r["net_avg"] is not None and r["net_avg"] >= net_min_filter]
    if be_max_filter is not None:
        out = [r for r in out if r["breakeven"] is not None and r["breakeven"] <= be_max_filter]
    # 평균예약률 높고 매물 많은 순. (최저예약률도 높으면 전 호실 검증된 건물)
    out.sort(key=lambda r: (-r["occ_avg"], -r["n"]))
    return jsonify({"total": len(out), "items": out})


@app.route("/api/trend")
def api_trend():
    """주간 스냅샷으로 지역(시도·시군구·동)별 예약률(1/2/3달) 추이 + 전주대비 Δ +
    그 동의 최고 인기 삼삼 오피스텔(현재 매물). 동별 베스트는 예약률 min_occ%(기본 20) 이상만."""
    a = request.args
    try:
        min_occ = float(a.get("min_occ", 20) or 20)
    except ValueError:
        min_occ = 20.0
    rows = []
    sido_f = set(_multi(a, "sido"))            # 복수 시/도
    sigungu_f = set(_multi(a, "sigungu"))
    try:
        # 계약=DB: samsam_snapshots 를 직접 조회. sido는 SQL에서, 시군구는 _city 정규화가
        # 필요해(예: '부천시 원미구'→'부천시') 파이썬에서 거른다(기존 파일 경로와 동일 의미).
        conn = db.connect()
        where, params = [], []
        if sido_f:
            where.append("sido = ANY(%s)"); params.append(list(sido_f))
        w = (" WHERE " + " AND ".join(where)) if where else ""
        rows = [dict(r) for r in conn.execute(
            "SELECT snapshot_date, sido, sigungu, dong, n, avg_occ_1m, avg_occ_2m, avg_occ_3m"
            f" FROM samsam_snapshots{w}", params).fetchall()]
        conn.close()
        if sigungu_f:
            rows = [r for r in rows if _city(r.get("sigungu")) in sigungu_f]
    except Exception as e:
        return jsonify({"dates": [], "items": [], "error": str(e)[:80]})

    # 동별: 현재 최고 인기 오피스텔 + 평균 월순수익(오피스텔, 보증금 1000 기준 네이버 매칭).
    top_office, net_by = {}, {}
    for r in L():
        if r.get("building_type") != "오피스텔":
            continue
        key = (r.get("sido") or "", r.get("sigungu") or "", r.get("dong") or "")
        occ = round((r.get("occ") or 0) * 100, 1)
        if occ >= min_occ:
            cur = top_office.get(key)
            if not cur or occ > cur["occ"]:
                top_office[key] = {"occ": occ, "name": r.get("name") or r.get("building_name") or "",
                                   "url": r.get("url") or ""}
        c = calc_at_deposit(r["room_id"], 1000, 0)
        if c is not None:
            net_by.setdefault(key, []).append(c["net"])
    avg_net = {k: round(statistics.mean(v), 1) for k, v in net_by.items() if v}

    dates = sorted({r["snapshot_date"] for r in rows})
    last = dates[-1] if dates else None
    # (sido,sigungu,dong) → date → [sum n*occ1, sum n*occ2, sum n*occ3, sum n]
    agg = {}
    for r in rows:
        key = (r.get("sido") or "", r.get("sigungu") or "", r.get("dong") or "")
        cell = agg.setdefault(key, {}).setdefault(r["snapshot_date"], [0.0, 0.0, 0.0, 0])
        nn = r.get("n") or 0
        cell[0] += (r.get("avg_occ_1m") or 0) * nn
        cell[1] += (r.get("avg_occ_2m") or 0) * nn
        cell[2] += (r.get("avg_occ_3m") or 0) * nn
        cell[3] += nn

    def _wavg(cell, i):
        return round(cell[i] / cell[3], 1) if cell and cell[3] else None

    out = []
    for (sido, sg, dong), dd in agg.items():
        series = {d: _wavg(c, 0) for d, c in dd.items()}   # 날짜별 1달 예약률(추이)
        lastcell = dd.get(last)
        occ1, occ2, occ3 = _wavg(lastcell, 0), _wavg(lastcell, 1), _wavg(lastcell, 2)
        prev = series.get(dates[-2]) if len(dates) >= 2 else None
        delta = round(occ1 - prev, 1) if (occ1 is not None and prev is not None) else None
        if occ1 is None or occ1 < min_occ:   # 예약률(1달) min_occ%+ 동만
            continue
        out.append({"sido": sido, "sigungu": sg, "dong": dong, "series": series,
                    "occ1": occ1, "occ2": occ2, "occ3": occ3, "delta": delta,
                    "n": lastcell[3] if lastcell else 0,
                    "net": avg_net.get((sido, sg, dong)),
                    "top_office": top_office.get((sido, sg, dong))})
    out.sort(key=lambda r: -(r["occ1"] or 0))
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


CHAT_DIST = os.path.join(ROOT, "frontend", "dist", "chat")   # 통합채팅 React 빌드(base /samsam/chat/)


@app.route("/chat/")
def chat_page():
    # 통합채팅 React 빌드 서빙. index.html은 문자열 반환(네비 주입 동작).
    idx = os.path.join(CHAT_DIST, "index.html")
    if os.path.exists(idx):
        with open(idx, encoding="utf-8") as f:
            return f.read()
    return ("<h3>채팅 프론트엔드 빌드가 없습니다.</h3>"
            "<p><code>cd frontend &amp;&amp; npm run build:chat</code> 후 새로고침하세요.</p>"), 200


@app.route("/chat/assets/<path:filename>")
def chat_assets(filename):
    return send_from_directory(os.path.join(CHAT_DIST, "assets"), filename)


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
