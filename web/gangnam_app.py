# -*- coding: utf-8 -*-
"""
네이버부동산 매물 뷰어 (Flask). 노출 지역은 common/target_regions.py 기준
(수도권 + 매일 크롤하는 추가 지역 — 2026-08-05 기준 부산·천안).

naver_listings(Supabase) 를 SQL로 조회(필터·페이지네이션)해 카드 그리드 + 상세 모달로 보여준다.
근처 삼삼(수요)은 samsam_listings(Supabase, 오피스텔) 인메모리 인덱스로 부착.

    python web/gangnam_app.py        # http://127.0.0.1:5002
"""
import math
import os
import sys

from flask import Flask, jsonify, request, send_from_directory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "frontend", "dist", "gangnam")   # React(Vite) 빌드 산출물
sys.path.insert(0, ROOT)   # db 모듈 import용(상세 모달이 DB에서 전체 컬럼을 가져옴)
sys.path.insert(0, os.path.join(ROOT, "common"))   # 공용 유틸(subway 등, sta-common 예정)
import subway  # noqa: E402  # 역 반경 검색: 매물 lat/lng ↔ 역 좌표 거리 계산
import db  # noqa: E402  # naver_listings 를 DB에서 직접 쿼리(70MB 파일 통짜 로드 대신)
import target_regions  # noqa: E402  # 크롤·노출 대상 지역(수도권 + 부산·천안)
# 역명(N역) → (lat, lng). data/subway_stations.csv(수도권 589역). '역 반경 검색' 자동완성·거리계산에 씀.
STATION_COORDS = {f"{n}역": (y, x) for n, y, x in subway._load()}
M2_PER_PYEONG = 3.305785

app = Flask(__name__)
from auth import init_auth  # noqa: E402
init_auth(app)

TYPE_NAMES = {
    "APT": "아파트", "OPST": "오피스텔", "VL": "빌라",
    "OR": "원룸", "DDDGG": "단독/다가구", "SG": "상가", "JWJT": "전원주택",
}

# 사이트가 읽는 소스: naver_listings(상세) 대신 실시간 뷰 nl_live.
#   nl_live = listings(실시간·전량·수도권·최근7일) LEFT JOIN naver_listings(상세 보강).
#   목록 크롤이 listings에 넣는 즉시 사이트에 뜨고, 상세는 채워지는 대로 자동 병합된다.
SRC = "nl_live"
# 집계/카운트용: 조인 없는 listings-only 뷰(같은 컬럼명, 인덱스로 빠름).
#   LEFT JOIN(naver_listings.article_no 유니크)이라 기본필터 count는 nl_live와 동일.
#   단 상세 전용 컬럼(rooms/주소/역명, office=상세 summary) 필터가 걸리면 nl_live로 카운트.
BASE = "nl_base"

# 집계(count/DISTINCT/stats)는 40만 행 뷰 전체 스캔이라 느림 → 짧은 TTL 캐시.
#   실시간 크롤 중이라도 몇 십 초 지연은 무방(카드 목록 자체는 캐시 없이 실시간).
import time as _time
_CACHE = {}
def _cached(key, ttl, fn):
    hit = _CACHE.get(key)
    if hit and (_time.time() - hit[0]) < ttl:
        return hit[1]
    val = fn()
    _CACHE[key] = (_time.time(), val)
    return val

# 업무용 오피스텔 판별: 부동산이 상세 summary에 '업무용' 또는 '전입(신고) 불가'라고 써놓은 것.
# 주의: '전입신고'만 매칭하면 '전입신고 가능'(주거용)까지 잡히므로, 반드시 '불가'가 붙은 문구만.
OFFICE_KW = ("업무용", "전입불가", "전입 불가", "전입신고 불가", "전입신고불가")


# 네이버 building_type_code → 삼삼 building_type. 같은 유형끼리만 비교(오피스텔↔오피스텔).
_NAVCODE2SAM = {"OPST": "오피스텔", "APT": "아파트", "VL": "연립빌라",
                "OR": "원룸건물", "DDDGG": "단독주택", "SG": "상가주택"}

_SAMOFF = None
def _sam_idx():
    """동 → [삼삼 매물 {btype, occ%, pyeong, week(주당 만원), name, url}]. (시군구,동)+동 키 둘 다.

    네이버 매물에 '근처(같은 동)·같은 유형·같은 평수' 삼삼 시세를 붙여 비교하게 한다.
    예전엔 삼삼 오피스텔만 색인해 네이버 원룸·빌라에도 오피스텔이 붙었음 → 전 유형 색인으로 교체."""
    global _SAMOFF
    if _SAMOFF is not None:
        return _SAMOFF
    idx = {}
    try:
        conn = db.connect()
        rows = conn.execute(
            "SELECT name, building_name, url, sigungu, dong, building_type, area_pyeong, "
            "rent_total_weekly, booked_days_1m, blocked_days_1m "
            "FROM samsam_listings").fetchall()
        conn.close()
    except Exception as e:
        print(f"[gangnam_app] 근처삼삼 DB 조회 실패({type(e).__name__}) → 빈 색인", flush=True)
        rows = []
    for r in rows:
        bk, bl = r["booked_days_1m"] or 0, r["blocked_days_1m"] or 0
        o = {"btype": r["building_type"] or "",
             "occ": round(min(1.0, bk / max(31 - bl, 1)) * 100, 1),
             "pyeong": r["area_pyeong"],
             "week": round((r["rent_total_weekly"] or 0) / 10000, 1),
             "name": r["name"] or r["building_name"] or "",
             "url": r["url"] or ""}
        sg, dong = r["sigungu"] or "", r["dong"] or ""
        for key in {(sg, dong), ("", dong)}:
            idx.setdefault(key, []).append(o)
    _SAMOFF = idx
    return idx


def _area_of(x):
    """이 네이버 매물 근처(같은 동)의 '같은 유형 + 같은 평수(정수 평 동일)' 삼삼 매물 비교.

    유형이 다르거나(오피스텔↔원룸 등) 평수가 다르면 비교군에서 제외 — 완전 동일 조건만.
    비교군이 없으면 None(비교 박스 미표시). 느슨한 '동 전체 폴백'은 오해를 낳아 제거."""
    sam_bt = _NAVCODE2SAM.get(x.get("building_type_code") or "")
    py = x.get("pyeong")
    if not sam_bt or not py:
        return None
    idx = _sam_idx()
    lst = idx.get((x.get("sigungu") or "", x.get("dong") or "")) or idx.get(("", x.get("dong") or "")) or []
    py_i = round(py)
    comp = [o for o in lst
            if o["btype"] == sam_bt and o["pyeong"] and round(o["pyeong"]) == py_i]
    if not comp:
        return None
    weeks = [o["week"] for o in comp if o["week"]]
    avg_week = round(sum(weeks) / len(weeks), 1) if weeks else None
    best = max(comp, key=lambda o: o["occ"])
    worst = min(comp, key=lambda o: o["occ"])
    pick = lambda o: {"name": o["name"], "occ": o["occ"], "week": o["week"], "url": o["url"]}
    res = {"n": len(comp), "same_pyeong": True, "pyeong": py_i, "btype": sam_bt,
           "avg_week": avg_week, "best": pick(best), "worst": pick(worst),
           "net": None, "sam_rev": None, "mgmt": None}
    # 순수익(월): 예약률 높은(잘나감) 동일조건 삼삼 매출 − 네이버 월세 − 관리비(없으면 기본 20).
    #   삼삼 월매출 = 주당 × 예약률 × 30/7 (build_integrated 관례).
    rent = x.get("rent_monthly")
    if best["week"] and isinstance(rent, (int, float)) and rent > 0:
        mm = x.get("maintenance_monthly")
        mgmt = mm if isinstance(mm, (int, float)) and mm > 0 else 20
        sam_rev = round(best["week"] * (best["occ"] / 100) * 30 / 7, 1)
        res.update(sam_rev=sam_rev, mgmt=mgmt, net=round(sam_rev - rent - mgmt, 1))
    return res


# ── DB(naver_listings) 조회 ──────────────────────────────────────────────────
# 예전엔 lab/naver_listings.jsonl(70MB)을 통짜로 메모리에 올려 파이썬으로 걸렀지만,
# 이제 필터·페이지네이션을 SQL(WHERE/LIMIT)로 밀어 DB에서 필요한 만큼만 가져온다.

# 목록/상세에 실제로 쓰는 컬럼만 SELECT(전 66컬럼 로드 안 함).
LIST_COLS = (
    "article_no", "building_name", "building_type_code", "sido", "sigungu", "dong",
    "deposit", "rent_monthly", "maintenance_monthly", "area_exclusive_m2",
    "floor_current", "rooms", "direction", "subway_station", "subway_distance_m",
    "summary", "lat", "lng", "jibun_address", "road_address", "confirmed_at",
)

# 시군구 문자열 → (시/군, 구) 를 SQL에서 계산.
#   "수원시 영통구" → ("수원시","영통구") · "강남구" → ("","강남구") · "화성시" → ("화성시","")
# LIKE '%구' 의 %가 파라미터 바인딩과 충돌하므로 right(..,1) 로 대체.
_SI_SQL = ("CASE WHEN position(' ' in sigungu) > 0 THEN split_part(sigungu, ' ', 1) "
           "WHEN right(sigungu, 1) = '구' THEN '' ELSE sigungu END")
_GU_SQL = ("CASE WHEN position(' ' in sigungu) > 0 THEN split_part(sigungu, ' ', 2) "
           "WHEN right(sigungu, 1) = '구' THEN sigungu ELSE '' END")

# 정렬 → SQL ORDER BY (월순수익 net_desc 는 삼삼 계산이 필요해 파이썬에서 처리).
_ORDER_SQL = {
    # 실시간 뷰(nl_live)에서 'recent'는 크롤 최신순(crawled_at, 인덱스 정렬로 빠름).
    # 등록확인일(confirmed_at)은 표현식이라 전체 정렬이 느려 기본 정렬로는 안 씀.
    "recent": "crawled_at DESC NULLS LAST",
    "rent_asc": "rent_monthly ASC NULLS LAST",
    "rent_desc": "rent_monthly DESC NULLS LAST",
    "deposit_asc": "deposit ASC NULLS LAST",
    "deposit_desc": "deposit DESC NULLS LAST",
    "area_asc": "area_exclusive_m2 ASC NULLS LAST",
    "area_desc": "area_exclusive_m2 DESC NULLS LAST",
}


def _enrich_row(d):
    """DB 행(dict) → 뷰에 필요한 파생(pyeong·url) 부착. (파일 버전 _load 와 동일)"""
    area = d.get("area_exclusive_m2")
    d["pyeong"] = round(area / M2_PER_PYEONG, 1) if isinstance(area, (int, float)) else None
    d["url"] = f"https://new.land.naver.com/offices?articleNo={d.get('article_no')}"
    return d


@app.route("/")
def index():
    # index.html은 '문자열'로 반환(auth.py after_request 네비 주입이 동작하려면 direct_passthrough 회피).
    idx = os.path.join(DIST, "index.html")
    if os.path.exists(idx):
        with open(idx, encoding="utf-8") as f:
            return f.read()
    return ("<h3>프론트엔드 빌드가 없습니다.</h3>"
            "<p><code>cd frontend &amp;&amp; npm run build:gangnam</code> 후 새로고침하세요.</p>"), 200


@app.route("/assets/<path:filename>")
def assets(filename):
    return send_from_directory(os.path.join(DIST, "assets"), filename)


@app.route("/api/facets")
def api_facets():
    # 첫 화면을 막지 않게 '가벼운 것만' 준다. 지역 트리는 /api/regions 로 lazy,
    # 업무용 매물수(느린 ILIKE 스캔)는 /api/office_count 로 async 로 뺐다.
    # sido는 작은 regions 테이블에서 — nl_live(29만) DISTINCT 전체 스캔 회피.
    # 대상 지역(수도권 + 부산·천안)만 노출. types는 고정 7종 — DISTINCT 스캔 회피.
    def _load():
        where, params = target_regions.sql_where()
        conn = db.connect()
        try:
            return [r[0] for r in conn.execute(
                f"SELECT DISTINCT sido FROM regions WHERE {where} ORDER BY sido",
                params).fetchall()]
        finally:
            conn.close()
    sidos = _cached("facets_sido", 600, _load)
    types = [{"code": c, "name": TYPE_NAMES[c]}
             for c in ["APT", "OPST", "VL", "OR", "DDDGG", "SG", "JWJT"]]
    return jsonify({"sido": sidos, "types": types,
                    "stations": sorted(STATION_COORDS.keys())})


@app.route("/api/regions")
def api_regions():
    """특정 시/도의 (시군구→동) 트리만 lazy 로드 — 지역 드롭다운을 처음부터 다 보내지 않으려고."""
    sido = request.args.get("sido", "").strip()
    if not sido:
        return jsonify([])
    def _load():
        conn = db.connect()
        try:
            return [list(r) for r in conn.execute(
                f"SELECT DISTINCT {_SI_SQL}, {_GU_SQL}, dong FROM {BASE} "
                "WHERE sido = %s AND dong IS NOT NULL AND dong <> '' ORDER BY 1, 2, 3",
                [sido]).fetchall()]
        finally:
            conn.close()
    return jsonify(_cached(f"regions:{sido}", 300, _load))   # [[시/군, 구, 동], ...]


@app.route("/api/office_count")
def api_office_count():
    """업무용 오피스텔 수 — summary ILIKE 전체 스캔이라 느려서 facets에서 빼 async로 분리."""
    def _load():
        conn = db.connect()
        try:
            office_kw = " OR ".join(["summary ILIKE %s"] * len(OFFICE_KW))
            return conn.execute(
                f"SELECT COUNT(*) FROM {SRC} "
                f"WHERE building_type_code = 'OPST' AND ({office_kw})",
                [f"%{k}%" for k in OFFICE_KW]).fetchone()[0]
        finally:
            conn.close()
    return jsonify({"office": _cached("office_count", 120, _load)})


@app.route("/api/stats")
def api_stats():
    def _load():
        conn = db.connect()
        try:
            total = conn.execute(f"SELECT COUNT(*) FROM {BASE}").fetchone()[0]
            dong_count = conn.execute(
                f"SELECT COUNT(DISTINCT dong) FROM {BASE} "
                "WHERE dong IS NOT NULL AND dong <> ''").fetchone()[0]
            # 정확한 중앙값은 40만행 정렬이라 수십 초 → listings 10% 표본으로 근사(뷰는 TABLESAMPLE 불가).
            med_where, med_params = target_regions.sql_where()
            med = conn.execute(
                "SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY rent) "
                "FROM listings TABLESAMPLE SYSTEM(10) "
                f"WHERE {med_where} "
                "AND crawled_at >= to_char(now() - interval '7 days','YYYY-MM-DD') "
                "AND rent > 0", med_params).fetchone()[0]
            by_type = [(r[0], r[1]) for r in conn.execute(
                f"SELECT building_type_code, COUNT(*) c FROM {BASE} "
                "GROUP BY building_type_code ORDER BY c DESC").fetchall()]
        finally:
            conn.close()
        return {
            "total": total,
            "dong_count": dong_count,
            "rent_median": med,
            "by_type": [{"code": k, "name": TYPE_NAMES.get(k, k), "count": c}
                        for k, c in by_type],
        }
    return jsonify(_cached("stats", 300, _load))


def _build_where(a):
    """요청 필터 → (SQL where 절 리스트, 파라미터). 역반경·월순수익은 여기서 안 다룸(파이썬 후처리)."""
    clauses, params = [], []

    types = [t for t in a.get("types", "").split(",") if t]
    if types:
        clauses.append("building_type_code = ANY(%s)")
        params.append(types)

    if a.get("sido", "").strip():
        clauses.append("sido = %s")
        params.append(a["sido"].strip())

    if a.get("sigun", "").strip():          # 시/군 (예: 수원시)
        clauses.append(f"{_SI_SQL} = %s")
        params.append(a["sigun"].strip())
    if a.get("gu", "").strip():             # 구 (예: 영통구 / 강남구)
        clauses.append(f"{_GU_SQL} = %s")
        params.append(a["gu"].strip())

    dongs = [d for d in a.get("dongs", "").split(",") if d]
    if dongs:
        clauses.append("dong = ANY(%s)")
        params.append(dongs)

    # 지역 다중선택: region=sido|sigun|gu|dong (빈 칸=와일드카드), 여러 개를 OR
    regs = [(r.split("|") + ["", "", "", ""])[:4] for r in a.getlist("region") if r]
    if regs:
        ors = []
        for sido_r, sigun_r, gu_r, dong_r in regs:
            sub = []
            if sido_r:
                sub.append("sido = %s"); params.append(sido_r)
            if sigun_r:
                sub.append(f"{_SI_SQL} = %s"); params.append(sigun_r)
            if gu_r:
                sub.append(f"{_GU_SQL} = %s"); params.append(gu_r)
            if dong_r:
                sub.append("dong = %s"); params.append(dong_r)
            ors.append("(" + (" AND ".join(sub) if sub else "TRUE") + ")")
        clauses.append("(" + " OR ".join(ors) + ")")

    # 방 개수: 1/2/3 정수(exact) 또는 '4+'(4개 이상)
    rooms_sel = a.getlist("rooms")
    if rooms_sel:
        exact = [int(r) for r in rooms_sel if r.isdigit()]
        parts = []
        if exact:
            parts.append("rooms = ANY(%s)"); params.append(exact)
        if "4+" in rooms_sel:
            parts.append("rooms >= 4")
        if parts:
            clauses.append("(" + " OR ".join(parts) + ")")

    # 범위(보증금·월세·평수). 평수는 area_exclusive_m2 로 환산해 필터.
    def rng(field, lo, hi, scale=1.0):
        if a.get(lo):
            clauses.append(f"{field} >= %s"); params.append(float(a[lo]) * scale)
        if a.get(hi):
            clauses.append(f"{field} <= %s"); params.append(float(a[hi]) * scale)
    rng("deposit", "deposit_min", "deposit_max")
    rng("rent_monthly", "rent_min", "rent_max")
    rng("area_exclusive_m2", "pyeong_min", "pyeong_max", scale=M2_PER_PYEONG)

    # 키워드(여러 필드 ILIKE)
    kw = a.get("keyword", "").strip()
    if kw:
        fields = ("building_name", "summary", "jibun_address", "road_address",
                  "subway_station", "summary_tags")
        clauses.append("(" + " OR ".join(f"{f} ILIKE %s" for f in fields) + ")")
        params.extend([f"%{kw}%"] * len(fields))

    # 업무용 오피스텔: 상세 summary에 '업무용'·'전입 불가' 명시한 OPST
    if a.get("office") == "1":
        clauses.append("building_type_code = 'OPST'")
        clauses.append("(" + " OR ".join(["summary ILIKE %s"] * len(OFFICE_KW)) + ")")
        params.extend([f"%{k}%" for k in OFFICE_KW])

    return clauses, params


def _sort_python(items, sort):
    """파이썬 경로(역반경/월순수익 관여 시)의 정렬 — 파일 버전 keyf 와 동일."""
    if sort == "net_desc":
        def netkey(x):
            n = (x.get("sam_area") or {}).get("net")
            return -(n if n is not None else -1e9)
        return sorted(items, key=netkey)
    if sort == "rent_asc":
        return sorted(items, key=lambda x: (_n(x.get("rent_monthly")) is None, _n(x.get("rent_monthly")) or 0))
    if sort == "rent_desc":
        return sorted(items, key=lambda x: -(_n(x.get("rent_monthly")) or 0))
    if sort == "deposit_asc":
        return sorted(items, key=lambda x: (_n(x.get("deposit")) is None, _n(x.get("deposit")) or 0))
    if sort == "deposit_desc":
        return sorted(items, key=lambda x: -(_n(x.get("deposit")) or 0))
    if sort == "area_desc":
        return sorted(items, key=lambda x: -(x.get("pyeong") or 0))
    if sort == "area_asc":
        return sorted(items, key=lambda x: (x.get("pyeong") is None, x.get("pyeong") or 0))
    return sorted(items, key=lambda x: x.get("confirmed_at") or "", reverse=True)


@app.route("/api/listings")
def api_listings():
    a = request.args
    clauses, params = _build_where(a)

    # 역 반경: bbox 로 SQL 선필터 → 파이썬에서 haversine 정밀 판정.
    stns = [s for s in a.getlist("station") if s in STATION_COORDS]
    radius = 1000.0
    if stns:
        try:
            radius = float(a.get("radius") or 1000)
        except ValueError:
            radius = 1000.0
        boxes = []
        for s in stns:
            la, ln = STATION_COORDS[s]
            dlat = radius / 111000.0
            dlng = radius / (111000.0 * max(math.cos(math.radians(la)), 0.01))
            boxes.append("(lat BETWEEN %s AND %s AND lng BETWEEN %s AND %s)")
            params.extend([la - dlat, la + dlat, ln - dlng, ln + dlng])
        clauses.append("(" + " OR ".join(boxes) + ")")

    sort = a.get("sort", "recent")
    try:
        net_min = float(a["net_min"]) if a.get("net_min") not in (None, "") else None
    except ValueError:
        net_min = None

    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    cols = ", ".join(LIST_COLS)
    page = max(1, int(a.get("page", 1)))
    size = min(120, max(1, int(a.get("size", 24))))

    # 역반경·월순수익이 걸리면 파이썬 후처리 필요 → SQL로 좁힌 전체를 가져와 처리.
    #   아니면 WHERE+ORDER BY+LIMIT 로 DB가 페이지만 돌려줌(빠름, 70MB 로드 없음).
    needs_python = bool(stns) or sort == "net_desc" or net_min is not None

    conn = db.connect()
    try:
        if not needs_python:
            # 상세전용 필터(방수/키워드/업무용)가 없으면 조인 없는 nl_base로 카운트(빠름).
            #   있으면 상세 컬럼이 필요하니 nl_live로 카운트.
            enriched = bool(a.getlist("rooms")) or bool(a.get("keyword", "").strip()) or a.get("office") == "1"
            count_src = SRC if enriched else BASE
            # 총 건수(페이지네이션용) → 필터 시그니처별 60초 캐시.
            total = _cached(f"cnt:{count_src}:{where_sql}:{params}", 60,
                            lambda: conn.execute(
                                f"SELECT COUNT(*) FROM {count_src}{where_sql}", params).fetchone()[0])
            order = _ORDER_SQL.get(sort, "crawled_at DESC NULLS LAST")
            rows = conn.execute(
                f"SELECT {cols} FROM {SRC}{where_sql} "
                f"ORDER BY {order} LIMIT %s OFFSET %s",
                params + [size, (page - 1) * size]).fetchall()
            items = [_enrich_row(dict(r)) for r in rows]
        else:
            items_all = [_enrich_row(dict(r)) for r in conn.execute(
                f"SELECT {cols} FROM {SRC}{where_sql}", params).fetchall()]
            if stns:   # bbox 로 좁힌 뒤 haversine 정밀 판정
                coords = [STATION_COORDS[s] for s in stns]
                items_all = [x for x in items_all
                             if x.get("lat") is not None and x.get("lng") is not None
                             and any(subway.haversine_m(x["lat"], x["lng"], sy, sx) <= radius
                                     for sy, sx in coords)]
            if net_min is not None or sort == "net_desc":
                for x in items_all:
                    x["sam_area"] = _area_of(x)
                if net_min is not None:
                    items_all = [x for x in items_all
                                 if (x.get("sam_area") or {}).get("net") is not None
                                 and x["sam_area"]["net"] >= net_min]
            items_all = _sort_python(items_all, sort)
            total = len(items_all)
            items = items_all[(page - 1) * size: (page - 1) * size + size]
    finally:
        conn.close()

    for it in items:   # 페이지 항목에 근처(같은 동) 삼삼 수요·순수익 부착(이미 있으면 재사용)
        if "sam_area" not in it:
            it["sam_area"] = _area_of(it)
    return jsonify({
        "total": total, "page": page, "size": size,
        "pages": (total + size - 1) // size,
        "items": items,
    })


@app.route("/api/detail/<int:no>")
def api_detail(no):
    """매물 1건 전체 컬럼(DB). 리스트 파일은 경량이라, 상세 모달만 클릭 시 DB에서 채운다."""
    try:
        import db
        conn = db.connect()
        # 상세 보강된 매물은 naver_listings 전체 컬럼, 아직 상세 안 된 실시간 매물은
        # nl_live(기본정보)로 폴백 — 상세 크롤이 채우기 전에도 모달이 뜨게.
        r = conn.execute("SELECT * FROM naver_listings WHERE article_no=%s", (no,)).fetchone()
        if not r:
            r = conn.execute(f"SELECT * FROM {SRC} WHERE article_no=%s", (no,)).fetchone()
        conn.close()
        if not r:
            return jsonify({})
        d = dict(r)
        area = d.get("area_exclusive_m2")
        d["pyeong"] = round(area / M2_PER_PYEONG, 1) if isinstance(area, (int, float)) else None
        d["url"] = f"https://new.land.naver.com/offices?articleNo={no}"
        return jsonify(d)
    except Exception as e:
        return jsonify({"error": str(e)[:100]})


def _n(v):
    return v if isinstance(v, (int, float)) else None


if __name__ == "__main__":
    import socket
    print("수도권 부동산 매물 뷰어 (실시간 뷰 nl_live)")
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        ip = "127.0.0.1"
    print("로컬:   http://127.0.0.1:5002")
    print(f"같은망: http://{ip}:5002")
    app.run(host="0.0.0.0", port=5002, debug=False)
