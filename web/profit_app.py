# -*- coding: utf-8 -*-
"""
삼삼엠투 × 네이버부동산 단기임대 수익성 뷰어 (마스터-디테일).

핵심: "삼삼 단기임대로 풀가동하면 네이버 장기월세 대비 얼마나 더 버나(최대수익·순수익)와
       그 지역(동/역) 수요(예약률)는 어떤가".
데이터: net_profit 테이블(Supabase) — build_integrated.py→export_net_profit.py 가 적재. 만원 단위.

용어(전부 '높을수록 좋음' 방향으로 통일):
- 최대수익 = 삼삼 월환산(주당×4.345, 풀가동 시 월 매출)
- 순수익   = 최대수익 − 네이버월총(환산월세+관리비)
- 예약률   = 1달 예약일 / (30 − 막힘일)   ← 공실률의 반대(직관적)
- 동예약률 = 같은 동 매칭매물들의 평균 예약률,  동경쟁매물수 = 같은 동 삼삼 매물수
"""
import json
import os
import statistics
import time

from flask import Flask, jsonify, request, send_from_directory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "frontend", "dist", "profit")   # React(Vite) 빌드 산출물(뷰어별)
app = Flask(__name__)
from auth import current_user, init_auth  # noqa: E402
# 데모 게이트: 미로그인도 수익성 뷰어(매물 탭)만 상위 일부를 볼 수 있게 → 회원가입 유도.
# 순위/추천/상세는 회원 전용. index·assets·facets·profit 만 허용.
init_auth(app, demo_endpoints={"index", "assets", "api_facets", "api_profit"})
DEMO_TOP_LOCK = 40   # 데모에서 가릴 상위(베스트) 매물 수 — 회원가입 유도(위는 잠그고 중간만 맛보기)
DEMO_LIMIT = 15      # 그 다음 노출할 '중간' 매물 수

# net_profit 테이블 컬럼(=웹 내부 짧은키). maxRev=풀가동(100%) 상한, realRev=실현(예약률 반영).
# (키는 export_net_profit.py 의 COL_MAP 값과 일치.)
PROFIT_MAP = {
    "삼삼ID": "id", "매물명": "name", "건물유형": "btype", "방수": "rooms",
    "시도": "sido", "시군구": "sigungu", "동": "dong", "인근역": "station",
    "동삼삼매물수": "dongCnt", "삼삼동일건물매물수": "samBldg", "평수": "pyeong",
    "삼삼주당_만원": "wk", "삼삼월환산_만원": "maxRev", "1달실현수익_만원": "realRev",
    "1달예약일": "bk", "1달막힘일": "bl",
    "네이버월세_만원": "nRent", "네이버보증금_만원": "nDep", "네이버환산월세_만원": "nEquiv",
    "네이버관리비_만원": "nMgmt", "관리비표기여부": "mgmtFlag", "네이버월총_만원": "nTotal",
    "매칭매물수": "matches", "네이버월총÷삼삼주당": "mult",
    "건물네이버매물수": "bldgCnt", "건물월세최저_만원": "bldgRentMin",
    "건물월세중간_만원": "bldgRentMed", "건물월세최고_만원": "bldgRentMax",
    "네이버건물": "bldg", "네이버링크": "naverUrl", "삼삼링크": "samUrl",
    "월별예약JSON": "monthOcc",   # 달력월별 예약 {'YYYY-MM':{bk,bl,days}} (앞으로 크롤분부터)
}
NUM = {"pyeong", "wk", "maxRev", "realRev", "bk", "bl", "nRent", "nDep", "nEquiv", "nMgmt",
       "nTotal", "matches", "mult", "dongCnt", "samBldg",
       "bldgCnt", "bldgRentMin", "bldgRentMed", "bldgRentMax"}


def _num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s in ("", "-") or s.startswith("미표기"):
        return None
    try:
        f = float(s)
        return int(f) if f.is_integer() else round(f, 2)
    except ValueError:
        return None


# net_profit 테이블 컬럼(웹 내부 짧은키와 동일). 계약=DB로 CSV 대신 이 테이블을 읽는다.
NP_COLS = list(PROFIT_MAP.values())


def load_profit():
    import db
    # Postgres는 따옴표 없는 식별자를 소문자로 낮추므로(dongCnt→dongcnt), 결과 키를 원래
    # 카멜케이스로 되돌리려 AS "카멜" 별칭을 준다.
    sel = ", ".join(f'{c.lower()} AS "{c}"' for c in NP_COLS)
    conn = db.connect()
    try:
        db_rows = conn.execute(f"SELECT {sel} FROM net_profit").fetchall()
    finally:
        conn.close()
    rows = []
    for r in db_rows:
        # DB 행 → 내부 dict. 숫자 컬럼은 정수/소수 정규화(_num), 문자 컬럼은 None→"".
        o = {}
        for key in NP_COLS:
            v = r[key]
            if key in NUM:
                o[key] = _num(v) if v is not None else None
            else:
                o[key] = v if v is not None else ""

        # 파생: 예약률 / 순수익(최대 기준)
        bk, bl = o.get("bk") or 0, o.get("bl") or 0
        # 수집 윈도우가 오늘~+30일 = 31일(양끝 포함)이라 분모도 31. 예약일+막힘일 ≤ 31 이라 ≤100%.
        avail = max(31 - bl, 1)
        o["occ"] = min(100.0, round(bk / avail * 100, 1))   # 예약률(%)
        # 실현매출 = 최대수익(풀가동 월매출) × 예약률. 이렇게 정의해야 예약률 100%일 때
        # 실현매출=최대수익, 기대월순수익=풀가동순수익으로 일치한다(세 지표의 일수 기준 불일치 해소).
        if o.get("maxRev") is not None:
            o["realRev"] = round(o["maxRev"] * o["occ"] / 100, 1)
        if o.get("maxRev") is not None and o.get("nTotal") is not None:
            o["net"] = round(o["maxRev"] - o["nTotal"], 1)  # 순수익(풀가동 상한: 최대−월총)
        else:
            o["net"] = None
        # 기대 월순수익 = 실현매출(예약률 반영) − 네이버월총. 예약률 0%면 −월총(손해).
        if o.get("realRev") is not None and o.get("nTotal") is not None:
            o["expNet"] = round(o["realRev"] - o["nTotal"], 1)
        else:
            o["expNet"] = None
        try:                                   # 달력월별 예약 JSON 파싱
            o["monthOcc"] = json.loads(o["monthOcc"]) if o.get("monthOcc") else {}
        except (ValueError, TypeError):
            o["monthOcc"] = {}
        rows.append(o)
    # 동/역 평균 예약률 부착
    _attach_area_occ(rows, "dong", "dongOcc")
    _attach_area_occ(rows, "station", "stOcc")
    return rows


def _attach_area_occ(rows, field, out):
    by = {}
    for r in rows:
        by.setdefault(r.get(field) or "", []).append(r["occ"])
    avg = {k: round(statistics.mean(v), 1) for k, v in by.items() if v}
    for r in rows:
        r[out] = avg.get(r.get(field) or "")


_CACHE = None
_CACHE_AT = 0.0
_CACHE_TTL = 300   # 5분마다 net_profit 재로드 → 데이터 갱신이 재배포 없이도 반영됨(웜 인스턴스 stale 방지).


def P():
    global _CACHE, _CACHE_AT
    now = time.time()
    if _CACHE is None or now - _CACHE_AT > _CACHE_TTL:
        _CACHE = load_profit()
        _CACHE_AT = now
    return _CACHE


@app.route("/")
def index():
    # React(Vite) 빌드를 서빙. index.html은 '문자열'로 반환해야 auth.py after_request의
    # 공통 네비게이션 주입(HTML 본문 수정)이 동작한다(send_file은 direct_passthrough라 주입 안 됨).
    idx = os.path.join(DIST, "index.html")
    if os.path.exists(idx):
        with open(idx, encoding="utf-8") as f:
            return f.read()
    return ("<h3>프론트엔드 빌드가 없습니다.</h3>"
            "<p><code>cd frontend &amp;&amp; npm run build</code> 후 새로고침하세요. "
            "(개발 중이면 <code>npm run dev</code> → http://localhost:5173/profit/)</p>"), 200


@app.route("/assets/<path:filename>")
def assets(filename):
    # Vite 빌드 에셋(js/css). base='/profit/'라 브라우저는 /profit/assets/* 로 요청 → 여기로 들어옴.
    return send_from_directory(os.path.join(DIST, "assets"), filename)


@app.route("/api/facets")
def api_facets():
    def uniq(k):
        return sorted({x.get(k) for x in P() if x.get(k)})
    tree = {}
    for x in P():
        tree.setdefault(x.get("sido", ""), {}).setdefault(x.get("sigungu", ""), set()).add(x.get("dong", ""))
    tree = {s: {g: sorted(d) for g, d in gg.items()} for s, gg in tree.items()}
    months = sorted({k for x in P() for k in (x.get("monthOcc") or {})})
    return jsonify({
        "sido": uniq("sido"), "tree": tree, "sigungu": uniq("sigungu"),
        "btype": uniq("btype"), "rooms": ["원룸", "투룸", "쓰리룸+"],
        "months": months,   # 달력월별 예약 데이터 있는 달(재크롤 전엔 빈 배열)
        "total": len(P()),
        "demo": current_user() is None,   # 미로그인=데모 모드(프론트가 게이트 표시)
    })


def _filter(a):
    items = list(_rows_for(a))   # 방 타입(rooms)·달력월(month) 반영된 기준 집합

    def eq(key, field):
        v = a.get(key)
        return [x for x in items if x.get(field) == v] if v else items

    for key, field in (("sido", "sido"), ("sigungu", "sigungu"),
                       ("dong", "dong"), ("btype", "btype"), ("rooms", "rooms")):
        items = [x for x in items if x.get(field) == a.get(key)] if a.get(key) else items

    kw = a.get("keyword", "").strip()
    if kw:
        items = [x for x in items if kw in (x.get("name") or "") or kw in (x.get("bldg") or "")]

    st = a.get("station", "").strip()   # 역 검색(인근역 부분일치)
    if st:
        items = [x for x in items if st in (x.get("station") or "")]
    dq = a.get("dong_kw", "").strip()   # 동 검색(부분일치 — 드롭다운 대신 타이핑)
    if dq:
        items = [x for x in items if dq in (x.get("dong") or "")]

    def fnum(key):
        v = a.get(key)
        try:
            return float(v) if v not in (None, "") else None
        except ValueError:
            return None

    def ge(field, key):
        nonlocal items
        v = fnum(key)
        if v is not None:
            items = [x for x in items if x.get(field) is not None and x[field] >= v]

    def le(field, key):
        nonlocal items
        v = fnum(key)
        if v is not None:
            items = [x for x in items if x.get(field) is not None and x[field] <= v]

    ge("net", "net_min"); ge("maxRev", "maxrev_min"); ge("occ", "occ_min")
    ge("dongOcc", "dongocc_min"); ge("pyeong", "pyeong_min"); le("pyeong", "pyeong_max")
    le("nDep", "dep_max"); ge("matches", "matches_min")
    return items


@app.route("/api/profit")
def api_profit():
    a = request.args

    # 데모(미로그인): 베스트(상위 DEMO_TOP_LOCK개)는 가리고 '중간'을 맛보기로 노출 →
    # "위에 더 좋은 게 있다"는 궁금증으로 회원가입 유도(혁신의숲 방식). 필터 무시.
    if current_user() is None:
        pool = [x for x in P() if (x.get("occ") or 0) >= 20]
        pool.sort(key=lambda x: -(x.get("expNet") if x.get("expNet") is not None else -1e9))
        top_lock = min(DEMO_TOP_LOCK, len(pool))
        shown = pool[top_lock: top_lock + DEMO_LIMIT]
        nets = [x["net"] for x in shown if x.get("net") is not None]
        occs = [x["occ"] for x in shown if x.get("occ") is not None]
        return jsonify({
            "demo": True, "top_locked": top_lock,
            "locked": max(0, len(pool) - top_lock - len(shown)), "total": len(shown),
            "page": 1, "size": DEMO_LIMIT, "pages": 1, "items": shown,
            "summary": {"count": len(P()), "net_med": _median(nets),
                        "net_max": max(nets) if nets else None, "occ_med": _median(occs)},
        })

    items = _filter(a)

    sort = a.get("sort", "net")
    rev = a.get("dir", "desc") != "asc"
    valid = set(PROFIT_MAP.values()) | {"occ", "net", "expNet", "dongOcc", "stOcc"}
    key = sort if sort in valid else "net"
    pres = [x for x in items if x.get(key) not in (None, "")]
    mis = [x for x in items if x.get(key) in (None, "")]
    pres.sort(key=lambda x: (x[key] if isinstance(x[key], (int, float)) else str(x[key]).lower()),
              reverse=rev)
    items = pres + mis

    nets = [x["net"] for x in items if x.get("net") is not None]
    occs = [x["occ"] for x in items if x.get("occ") is not None]
    summary = {
        "count": len(items),
        "net_med": _median(nets), "net_max": max(nets) if nets else None,
        "occ_med": _median(occs),
    }
    page = max(1, int(a.get("page", 1)))
    size = min(300, int(a.get("size", 40)))
    total = len(items)
    return jsonify({"summary": summary, "total": total, "page": page, "size": size,
                    "pages": (total + size - 1) // size,
                    "items": items[(page - 1) * size: page * size]})


def _agg(groups):
    """{label: [rows]} → 그룹별 집계(매칭수·경쟁삼삼·평균 순수익/예약률/최대수익). 순수익 높은 순."""
    out = []
    for label, xs in groups.items():
        nets = [v["net"] for v in xs if v.get("net") is not None]
        exps = [v["expNet"] for v in xs if v.get("expNet") is not None]
        occs = [v["occ"] for v in xs if v.get("occ") is not None]
        maxs = [v["maxRev"] for v in xs if v.get("maxRev") is not None]
        comps = [v["dongCnt"] for v in xs if v.get("dongCnt") is not None]
        out.append({
            "name": label, "n": len(xs),
            "comp": max(comps) if comps else len(xs),   # 경쟁(그 지역 삼삼 매물수)
            "net": round(statistics.mean(nets), 1) if nets else None,        # 풀가동 순수익
            "expNet": round(statistics.mean(exps), 1) if exps else None,     # 기대 월순수익(예약률 반영)
            "occ": round(statistics.mean(occs), 1) if occs else None,
            "maxRev": round(statistics.mean(maxs), 1) if maxs else None,
        })
    # 기본 정렬 = 기대 월순수익(예약률 반영). 예약률 0%면 −월총이라 자연히 하위로.
    out.sort(key=lambda r: (r["expNet"] is None, -(r["expNet"] if r["expNet"] is not None else -1e9)))
    return out


def _rows_for(a):
    """공통 필터: 방 타입(rooms) + 달력월(month). rooms는 같은 동도 원룸/투룸 예약률이 달라서,
    month는 '2026-08처럼 특정 달' 예약률/기대순수익을 보기 위해(롤링 1달 대신). month 지정 시
    그 달 데이터(monthOcc) 있는 매물만, occ/실현매출/기대순수익을 그 달 기준으로 재계산."""
    rm = a.get("rooms")
    base = [x for x in P() if x.get("rooms") == rm] if rm else P()
    month = (a.get("month") or "").strip()
    if not month:
        return base
    out = []
    for x in base:
        mo = (x.get("monthOcc") or {}).get(month)
        if not mo:
            continue   # 그 달 예약 데이터 없는 매물 제외(재크롤 전엔 전부 비어 있음)
        bk, bl, days = mo.get("bk") or 0, mo.get("bl") or 0, mo.get("days") or 30
        y = dict(x)
        y["occ"] = min(100.0, round(bk / max(days - bl, 1) * 100, 1))
        # 그 달 실현매출 = 최대수익 × 그 달 예약률 (전체 경로와 동일 정의 — 100%면 실현=최대).
        y["realRev"] = round((y.get("maxRev") or 0) * y["occ"] / 100, 1)
        y["expNet"] = round(y["realRev"] - y["nTotal"], 1) if y.get("nTotal") is not None else None
        out.append(y)
    return out


# 순위 라벨에서 서울/인천은 시군구가 '강남구'처럼 구만이라 어느 시인지 안 보임(QA #4).
# → 시/도를 축약해 앞에 붙인다. 경기는 시군구에 이미 시(수원시)가 들어 있어 '경기 수원시 …'가 됨.
_SIDO_SHORT = {"서울특별시": "서울", "경기도": "경기", "인천광역시": "인천"}


def _group(field_or_key, rows=None):
    """rows(기본 P())를 동(시도+시군구+동)/역 단위로 묶는다. dong은 구 다르면 분리(같은 동명 병합 방지)."""
    by = {}
    for x in (P() if rows is None else rows):
        if field_or_key == "dong":
            sg, dg = x.get("sigungu") or "", x.get("dong") or ""
            if not dg:
                continue
            sido = _SIDO_SHORT.get(x.get("sido") or "", x.get("sido") or "")
            by.setdefault(f"{sido} {sg} {dg}".strip(), []).append(x)
        else:
            k = x.get(field_or_key) or ""
            if not k:
                continue
            by.setdefault(k, []).append(x)
    return by


@app.route("/api/rank")
def api_rank():
    """동별·역별 순위 — 어디서 운영하는 게 제일 좋은지(평균 순수익/예약률/최대수익 + 경쟁 삼삼 매물수).
    동은 '시군구 동'으로 묶어 같은 동명(구 다른)이 섞이지 않게 한다. rooms로 방 타입별 조회 가능."""
    rows = _rows_for(request.args)
    return jsonify({"dong": _agg(_group("dong", rows)), "station": _agg(_group("station", rows))})


@app.route("/api/rank_detail")
def api_rank_detail():
    """순위(동/역) 한 행의 '근거 매물' 목록 — 그 동/역을 이루는 삼삼 매물과 각자의 부동산 매칭(QA #8).
    사용자가 순위 수치가 어떤 매물들로 산출됐는지 직접 확인하게 한다. rooms/month는 순위와 동일하게 반영."""
    a = request.args
    field = "dong" if a.get("field") == "dong" else "station"
    label = a.get("label", "")
    rows = _rows_for(a)
    items = _group(field, rows).get(label, [])
    out = [{
        "name": x.get("name") or "",
        "area": f"{x.get('sigungu','')} {x.get('dong','')}".strip(),
        "station": x.get("station") or "", "pyeong": x.get("pyeong"),
        "wk": x.get("wk"), "occ": x.get("occ"),
        "expNet": x.get("expNet"), "net": x.get("net"), "maxRev": x.get("maxRev"),
        "matches": x.get("matches"),
        "nRent": x.get("nRent"), "nDep": x.get("nDep"), "nTotal": x.get("nTotal"),
        "samUrl": x.get("samUrl") or "", "naverUrl": x.get("naverUrl") or "",
    } for x in items]
    out.sort(key=lambda r: -(r["expNet"] if r["expNet"] is not None else -1e9))
    return jsonify({"label": label, "field": field, "total": len(out), "items": out})


@app.route("/api/recommend")
def api_recommend():
    """신규진입 추천(블루오션): 수요(예약률) 있고 기대 월순수익 좋은데 경쟁 삼삼 매물이 적은 동/역/오피스텔.
    기회점수 = 기대월순수익(예약률 반영) ÷ √경쟁수 (수익·수요 높을수록↑, 경쟁 많을수록↓)."""
    a = request.args

    def fnum(key, default=None):
        try:
            return float(a[key]) if a.get(key) not in (None, "") else default
        except ValueError:
            return default
    min_occ = fnum("min_occ", 30)      # 최소 평균예약률(수요 검증)
    min_n = int(fnum("min_n", 2))       # 최소 표본(매칭 매물수) — 노이즈 제외
    max_comp = fnum("max_comp", None)   # 최대 경쟁 삼삼 매물수(비우면 제한 없음)
    rows = _rows_for(a)                 # 방 타입(원룸/투룸/쓰리룸+)별 조회

    def score(expnet, comp):
        if expnet is None or expnet <= 0:   # 기대 월순수익이 양(+)일 때만(예약률 반영)
            return None
        return round(expnet / max(comp, 1) ** 0.5, 1)

    def build(groups):
        out = []
        for r in _agg(groups):
            if r["n"] < min_n or r["occ"] is None or r["occ"] < min_occ:
                continue
            if max_comp is not None and r["comp"] > max_comp:
                continue
            sc = score(r["expNet"], r["comp"])
            if sc is None:
                continue
            out.append({**r, "score": sc})
        out.sort(key=lambda r: -r["score"])
        return out

    # 오피스텔: 개별 매물(오피스텔 유형) 중 수요·수익 좋고 그 동 경쟁 적은 것. 경쟁=동삼삼매물수.
    offices = []
    for x in rows:
        if "오피스텔" not in (x.get("btype") or ""):
            continue
        if x.get("occ") is None or x["occ"] < min_occ or x.get("expNet") is None or x["expNet"] <= 0:
            continue
        comp = x.get("dongCnt") or 1
        if max_comp is not None and comp > max_comp:
            continue
        offices.append({
            "name": x.get("name") or "", "dong": f"{x.get('sigungu','')} {x.get('dong','')}".strip(),
            "station": x.get("station") or "", "pyeong": x.get("pyeong"),
            "comp": comp, "occ": x["occ"], "net": x["net"], "expNet": x["expNet"], "maxRev": x.get("maxRev"),
            "matches": x.get("matches"),   # 부동산 매칭 표본수(신뢰도) — QA #11
            "score": score(x["expNet"], comp),
            "samUrl": x.get("samUrl") or "", "naverUrl": x.get("naverUrl") or "",
        })
    offices.sort(key=lambda r: -(r["score"] or 0))
    return jsonify({"dong": build(_group("dong", rows)), "station": build(_group("station", rows)),
                    "office": offices[:200]})


def _median(xs):
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else round((xs[n // 2 - 1] + xs[n // 2]) / 2, 1)


if __name__ == "__main__":
    print(f"수익성 매칭 {len(P())}건 로드 / http://127.0.0.1:5001")
    app.run(host="0.0.0.0", port=5001, debug=False)
