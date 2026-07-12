# -*- coding: utf-8 -*-
"""
수도권 네이버부동산 매물 뷰어 (Flask).

lab/naver_listings.jsonl (naver_listings 스키마, 서울·경기·인천 전 유형)을
그대로 읽어 카드 그리드 + 상세 모달로 보여준다. DB 불필요(로컬 jsonl만 읽음).
export: python pipeline/naver/export_jsonl.py

    python web/gangnam_app.py        # http://127.0.0.1:5002
"""
import json
import os
import sys

from flask import Flask, jsonify, render_template, request, send_from_directory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "frontend", "dist", "gangnam")   # React(Vite) 빌드 산출물
sys.path.insert(0, ROOT)   # db 모듈 import용(상세 모달이 DB에서 전체 컬럼을 가져옴)
sys.path.insert(0, os.path.join(ROOT, "pipeline", "naver"))   # subway(역 좌표·하버사인)
import subway  # noqa: E402  # 역 반경 검색: 매물 lat/lng ↔ 역 좌표 거리 계산
# 역명(N역) → (lat, lng). data/subway_stations.csv(수도권 589역). '역 반경 검색' 자동완성·거리계산에 씀.
STATION_COORDS = {f"{n}역": (y, x) for n, y, x in subway._load()}
DATA = os.path.join(ROOT, "lab", "naver_listings.jsonl")
SAMSAM = os.path.join(ROOT, "lab", "samsam_listings.jsonl")   # 근처 삼삼(단기임대) 수요 붙이기용
M2_PER_PYEONG = 3.305785

app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"))
from auth import init_auth  # noqa: E402
init_auth(app)

TYPE_NAMES = {
    "APT": "아파트", "OPST": "오피스텔", "VL": "빌라",
    "OR": "원룸", "DDDGG": "단독/다가구", "SG": "상가",
}

# 업무용 오피스텔 판별: 부동산이 상세 summary에 '업무용' 또는 '전입(신고) 불가'라고 써놓은 것.
# 주의: '전입신고'만 매칭하면 '전입신고 가능'(주거용)까지 잡히므로, 반드시 '불가'가 붙은 문구만.
OFFICE_KW = ("업무용", "전입불가", "전입 불가", "전입신고 불가", "전입신고불가")


def _is_office(x):
    return x.get("building_type_code") == "OPST" and \
        any(k in (x.get("summary") or "") for k in OFFICE_KW)


def _split_sgg(sigungu):
    """'수원시 영통구'→('수원시','영통구') · '강남구'→('','강남구') · '화성시'→('화성시','') ·
    '가평군'→('가평군','')  (시/군 과 구 분리)."""
    parts = (sigungu or "").split()
    if len(parts) >= 2:
        return parts[0], parts[1]
    p = parts[0] if parts else ""
    return ("", p) if p.endswith("구") else (p, "")


PYEONG_TOL = 3   # '평수도 같은' 허용 오차(평)

_SAMOFF = None
def _sam_offices():
    """동 → [삼삼 오피스텔 {occ%, pyeong, week(주당 만원), name, url}]. (시군구,동)+동 키 둘 다.

    네이버 매물에 '근처(같은 동)·같은 평수 삼삼 오피스텔'의 주당 평균·잘나가는/안나가는 걸
    붙여, 삼삼엔 없어도 근처라 괜찮을지 가늠하게 한다. lab/samsam_listings.jsonl(오피스텔)로 색인."""
    global _SAMOFF
    if _SAMOFF is not None:
        return _SAMOFF
    idx = {}
    if os.path.exists(SAMSAM):
        with open(SAMSAM, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("building_type") != "오피스텔":
                    continue
                bk, bl = r.get("booked_days_1m") or 0, r.get("blocked_days_1m") or 0
                o = {"occ": round(min(1.0, bk / max(31 - bl, 1)) * 100, 1),
                     "pyeong": r.get("area_pyeong"),
                     "week": round((r.get("rent_total_weekly") or 0) / 10000, 1),
                     "name": r.get("name") or r.get("building_name") or "",
                     "url": r.get("url") or ""}
                sg, dong = r.get("sigungu") or "", r.get("dong") or ""
                for key in {(sg, dong), ("", dong)}:
                    idx.setdefault(key, []).append(o)
    _SAMOFF = idx
    return idx


def _area_of(x):
    """이 네이버 매물 근처(같은 동)·같은 평수 삼삼 오피스텔: 주당 평균 + 잘나가는/안나가는 것."""
    idx = _sam_offices()
    lst = idx.get((x.get("sigungu") or "", x.get("dong") or "")) or idx.get(("", x.get("dong") or "")) or []
    if not lst:
        return None
    py = x.get("pyeong")
    same = [o for o in lst if py and o["pyeong"] and abs(o["pyeong"] - py) <= PYEONG_TOL]
    comp = same or lst                      # 같은 평수 없으면 동 전체로 폴백
    weeks = [o["week"] for o in comp if o["week"]]
    avg_week = round(sum(weeks) / len(weeks), 1) if weeks else None
    best = max(comp, key=lambda o: o["occ"])
    worst = min(comp, key=lambda o: o["occ"])
    pick = lambda o: {"name": o["name"], "occ": o["occ"], "week": o["week"], "url": o["url"]}
    res = {"n": len(comp), "same_pyeong": bool(same), "avg_week": avg_week,
           "best": pick(best), "worst": pick(worst),
           "net": None, "sam_rev": None, "mgmt": None}
    # 순수익(월): 예약률 높은(잘나감) 삼삼 오피스텔 매출 − 네이버 월세 − 관리비(없으면 기본 20).
    #   삼삼 월매출 = 주당 × 예약률 × 30/7 (build_integrated 관례).
    rent = x.get("rent_monthly")
    if best["week"] and isinstance(rent, (int, float)) and rent > 0:
        mm = x.get("maintenance_monthly")
        mgmt = mm if isinstance(mm, (int, float)) and mm > 0 else 20
        sam_rev = round(best["week"] * (best["occ"] / 100) * 30 / 7, 1)
        res.update(sam_rev=sam_rev, mgmt=mgmt, net=round(sam_rev - rent - mgmt, 1))
    return res


def _load():
    rows = []
    if not os.path.exists(DATA):
        return rows
    with open(DATA, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            area = r.get("area_exclusive_m2")
            r["pyeong"] = round(area / M2_PER_PYEONG, 1) if isinstance(area, (int, float)) else None
            r["url"] = f"https://new.land.naver.com/offices?articleNo={r.get('article_no')}"
            rows.append(r)
    return rows


_CACHE=None
def L():
    global _CACHE
    if _CACHE is None:
        _CACHE=_load()
    return _CACHE


@app.route("/")
def index():
    # React(Vite) 빌드를 서빙. 빌드 전이면 안내.
    idx = os.path.join(DIST, "index.html")
    if os.path.exists(idx):
        return send_from_directory(DIST, "index.html")
    return ("<h3>프론트엔드 빌드가 없습니다.</h3>"
            "<p><code>cd frontend &amp;&amp; npm run build:gangnam</code> 후 새로고침하세요.</p>"), 200


@app.route("/assets/<path:filename>")
def assets(filename):
    return send_from_directory(os.path.join(DIST, "assets"), filename)


@app.route("/api/facets")
def api_facets():
    dongs = sorted({x.get("dong") for x in L() if x.get("dong")})
    # 도→시/군→구→동 트리(시·도·구 분리해 좁혀 선택). 경기 '수원시 영통구'를 시/구로 나눔.
    regions = sorted({(x.get("sido") or "", *_split_sgg(x.get("sigungu")), x.get("dong") or "")
                      for x in L() if x.get("dong")})
    types = [{"code": c, "name": TYPE_NAMES.get(c, c)}
             for c in ["APT", "OPST", "VL", "OR", "DDDGG", "SG"]
             if any(x.get("building_type_code") == c for x in L())]
    office_n = sum(1 for x in L() if _is_office(x))
    return jsonify({"dongs": dongs, "regions": regions, "types": types,
                    "stations": sorted(STATION_COORDS.keys()),
                    "total": len(L()), "office": office_n})


@app.route("/api/stats")
def api_stats():
    by_type, by_dong, rents = {}, {}, []
    for x in L():
        t = x.get("building_type_code")
        by_type[t] = by_type.get(t, 0) + 1
        d = x.get("dong")
        by_dong[d] = by_dong.get(d, 0) + 1
        rm = x.get("rent_monthly")
        if isinstance(rm, (int, float)) and rm > 0:
            rents.append(rm)
    rents.sort()
    med = rents[len(rents) // 2] if rents else None
    return jsonify({
        "total": len(L()),
        "dong_count": len(by_dong),
        "rent_median": med,
        "by_type": [{"code": k, "name": TYPE_NAMES.get(k, k), "count": v}
                    for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])],
    })


@app.route("/api/listings")
def api_listings():
    a = request.args
    items = L()

    types = [t for t in a.get("types", "").split(",") if t]
    if types:
        items = [x for x in items if x.get("building_type_code") in types]

    sido = a.get("sido", "").strip()
    if sido:
        items = [x for x in items if x.get("sido") == sido]
    sigun = a.get("sigun", "").strip()   # 시/군 (예: 수원시)
    gu = a.get("gu", "").strip()          # 구 (예: 영통구 / 강남구)
    if sigun or gu:
        def _match(x):
            si, g = _split_sgg(x.get("sigungu"))
            return (not sigun or si == sigun) and (not gu or g == gu)
        items = [x for x in items if _match(x)]

    dongs = [d for d in a.get("dongs", "").split(",") if d]
    if dongs:
        items = [x for x in items if x.get("dong") in dongs]

    # 지역 다중선택: region 파라미터 반복(각 "sido|sigun|gu|dong", 빈 칸은 와일드카드).
    # 여러 시도/구/동을 섞어 union(OR). 예: region=서울시||강남구|역삼동 & region=경기||| 처럼.
    regs = [r.split("|") for r in a.getlist("region") if r]
    if regs:
        regs = [(r + ["", "", "", ""])[:4] for r in regs]   # 4칸 패딩

        def _rmatch(x):
            si, g = _split_sgg(x.get("sigungu"))
            for sido, sigun, gu, dong in regs:
                if sido and x.get("sido") != sido:
                    continue
                if sigun and si != sigun:
                    continue
                if gu and g != gu:
                    continue
                if dong and x.get("dong") != dong:
                    continue
                return True
            return False
        items = [x for x in items if _rmatch(x)]

    # 방 개수 다중선택: rooms 파라미터 반복(1/2/3 정수 또는 '4+'=4개 이상).
    rooms_sel = a.getlist("rooms")
    if rooms_sel:
        exact = {int(r) for r in rooms_sel if r.isdigit()}
        fourplus = "4+" in rooms_sel

        def _rmroom(x):
            rc = x.get("rooms")
            return rc is not None and (rc in exact or (fourplus and rc >= 4))
        items = [x for x in items if _rmroom(x)]

    # 역 반경 검색: station 파라미터 반복(역명) + radius(m, 기본 1000). 선택 역들 반경 내 매물 union.
    stns = [s for s in a.getlist("station") if s in STATION_COORDS]
    if stns:
        try:
            radius = float(a.get("radius") or 1000)
        except ValueError:
            radius = 1000.0
        coords = [STATION_COORDS[s] for s in stns]

        def _near(x):
            la, ln = x.get("lat"), x.get("lng")
            if la is None or ln is None:
                return False
            return any(subway.haversine_m(la, ln, sy, sx) <= radius for sy, sx in coords)
        items = [x for x in items if _near(x)]

    def rng(field, lo, hi):
        nonlocal items
        if a.get(lo):
            v = float(a[lo]); items = [x for x in items if _n(x.get(field)) is not None and _n(x.get(field)) >= v]
        if a.get(hi):
            v = float(a[hi]); items = [x for x in items if _n(x.get(field)) is not None and _n(x.get(field)) <= v]

    rng("deposit", "deposit_min", "deposit_max")
    rng("rent_monthly", "rent_min", "rent_max")
    rng("pyeong", "pyeong_min", "pyeong_max")

    kw = a.get("keyword", "").strip()
    if kw:
        def hit(x):
            for f in ("building_name", "summary", "jibun_address", "road_address",
                      "subway_station", "tags"):
                v = x.get(f)
                if v and kw in str(v):
                    return True
            return False
        items = [x for x in items if hit(x)]

    # 업무용 오피스텔: 부동산이 상세에 '업무용'·'전입신고 불가'라고 명시한 오피스텔만.
    if a.get("office") == "1":
        items = [x for x in items if _is_office(x)]

    sort = a.get("sort", "recent")
    try:
        net_min = float(a["net_min"]) if a.get("net_min") not in (None, "") else None
    except ValueError:
        net_min = None
    # 월순수익 필터/정렬은 매물별 순수익이 필요 → 그때만 전체 대상으로 계산(캐시).
    if net_min is not None or sort == "net_desc":
        for x in items:
            if "sam_area" not in x:
                x["sam_area"] = _area_of(x)
        if net_min is not None:
            items = [x for x in items if (x.get("sam_area") or {}).get("net") is not None
                     and x["sam_area"]["net"] >= net_min]

    def _netkey(x):
        n = (x.get("sam_area") or {}).get("net")
        return -(n if n is not None else -1e9)
    keyf = {
        "rent_asc": lambda x: (_n(x.get("rent_monthly")) is None, _n(x.get("rent_monthly")) or 0),
        "rent_desc": lambda x: -(_n(x.get("rent_monthly")) or 0),
        "deposit_asc": lambda x: (_n(x.get("deposit")) is None, _n(x.get("deposit")) or 0),
        "deposit_desc": lambda x: -(_n(x.get("deposit")) or 0),
        "area_desc": lambda x: -(_n(x.get("pyeong")) or 0),
        "area_asc": lambda x: (_n(x.get("pyeong")) is None, _n(x.get("pyeong")) or 0),
        "net_desc": _netkey,
        "recent": lambda x: x.get("confirmed_at") or "",
    }.get(sort, lambda x: x.get("confirmed_at") or "")
    rev = sort in ("recent",)
    items = sorted(items, key=keyf, reverse=rev)

    total = len(items)
    page = max(1, int(a.get("page", 1)))
    size = min(120, max(1, int(a.get("size", 24))))
    start = (page - 1) * size
    page_items = items[start:start + size]
    for it in page_items:      # 이 매물 근처(같은 동) 삼삼 수요·순수익 부착(이미 있으면 재사용)
        if "sam_area" not in it:
            it["sam_area"] = _area_of(it)
    return jsonify({
        "total": total, "page": page, "size": size,
        "pages": (total + size - 1) // size,
        "items": page_items,
    })


@app.route("/api/detail/<int:no>")
def api_detail(no):
    """매물 1건 전체 컬럼(DB). 리스트 파일은 경량이라, 상세 모달만 클릭 시 DB에서 채운다."""
    try:
        import db
        conn = db.connect()
        r = conn.execute("SELECT * FROM naver_listings WHERE article_no=%s", (no,)).fetchone()
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
    print(f"수도권 부동산 매물 {len(L())}건 로드됨")
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        ip = "127.0.0.1"
    print("로컬:   http://127.0.0.1:5002")
    print(f"같은망: http://{ip}:5002")
    app.run(host="0.0.0.0", port=5002, debug=False)
