# -*- coding: utf-8 -*-
"""
삼삼엠투 × 네이버부동산 단기임대 수익성 뷰어 (마스터-디테일).

핵심: "삼삼 단기임대로 풀가동하면 네이버 장기월세 대비 얼마나 더 버나(최대수익·순수익)와
       그 지역(동/역) 수요(예약률)는 어떤가".
데이터: data/net_profit_integrated.csv (build_integrated.py 산출, 만원 단위)

용어(전부 '높을수록 좋음' 방향으로 통일):
- 최대수익 = 삼삼 월환산(주당×4.345, 풀가동 시 월 매출)
- 순수익   = 최대수익 − 네이버월총(환산월세+관리비)
- 예약률   = 1달 예약일 / (30 − 막힘일)   ← 공실률의 반대(직관적)
- 동예약률 = 같은 동 매칭매물들의 평균 예약률,  동경쟁매물수 = 같은 동 삼삼 매물수
"""
import csv
import os
import statistics

from flask import Flask, jsonify, render_template, request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"))
from auth import init_auth  # noqa: E402
init_auth(app)

# CSV 원본 헤더 → 짧은 키 (실현/현실효율/CSV순수익은 안 씀 — 최대수익 기준으로 재계산)
PROFIT_MAP = {
    "삼삼ID": "id", "매물명": "name", "건물유형": "btype", "방수": "rooms",
    "시도": "sido", "시군구": "sigungu", "동": "dong", "인근역": "station",
    "동삼삼매물수": "dongCnt", "삼삼동일건물매물수": "samBldg", "평수": "pyeong",
    "삼삼주당_만원": "wk", "삼삼월환산_만원": "maxRev",
    "1달예약일": "bk", "1달막힘일": "bl",
    "네이버월세_만원": "nRent", "네이버보증금_만원": "nDep", "네이버환산월세_만원": "nEquiv",
    "네이버관리비_만원": "nMgmt", "관리비표기여부": "mgmtFlag", "네이버월총_만원": "nTotal",
    "매칭매물수": "matches", "네이버월총÷삼삼주당": "mult",
    "건물네이버매물수": "bldgCnt", "건물월세최저_만원": "bldgRentMin",
    "건물월세중간_만원": "bldgRentMed", "건물월세최고_만원": "bldgRentMax",
    "네이버건물": "bldg", "네이버링크": "naverUrl", "삼삼링크": "samUrl",
}
NUM = {"pyeong", "wk", "maxRev", "bk", "bl", "nRent", "nDep", "nEquiv", "nMgmt",
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


def load_profit():
    path = os.path.join(DATA, "net_profit_integrated.csv")
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            o = {key: (None if key in NUM else "") for key in PROFIT_MAP.values()}
            for kr, key in PROFIT_MAP.items():
                if kr in r:
                    o[key] = _num(r[kr]) if key in NUM else (r[kr] or "")
            # 파생: 예약률 / 순수익(최대 기준)
            bk, bl = o.get("bk") or 0, o.get("bl") or 0
            # 수집 윈도우가 오늘~+30일 = 31일(양끝 포함)이라 분모도 31. 예약일+막힘일 ≤ 31 이라 ≤100%.
            avail = max(31 - bl, 1)
            o["occ"] = min(100.0, round(bk / avail * 100, 1))   # 예약률(%)
            if o.get("maxRev") is not None and o.get("nTotal") is not None:
                o["net"] = round(o["maxRev"] - o["nTotal"], 1)  # 순수익(최대−월총)
            else:
                o["net"] = None
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


_CACHE=None
def P():
    global _CACHE
    if _CACHE is None:
        _CACHE=load_profit()
    return _CACHE


@app.route("/")
def index():
    return render_template("profit.html")


@app.route("/api/facets")
def api_facets():
    def uniq(k):
        return sorted({x.get(k) for x in P() if x.get(k)})
    tree = {}
    for x in P():
        tree.setdefault(x.get("sido", ""), {}).setdefault(x.get("sigungu", ""), set()).add(x.get("dong", ""))
    tree = {s: {g: sorted(d) for g, d in gg.items()} for s, gg in tree.items()}
    return jsonify({
        "sido": uniq("sido"), "tree": tree, "sigungu": uniq("sigungu"),
        "btype": uniq("btype"), "rooms": ["원룸", "투룸", "쓰리룸+"],
        "total": len(P()),
    })


def _filter(a):
    items = list(P())

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
    items = _filter(a)

    sort = a.get("sort", "net")
    rev = a.get("dir", "desc") != "asc"
    valid = set(PROFIT_MAP.values()) | {"occ", "net", "dongOcc", "stOcc"}
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
        occs = [v["occ"] for v in xs if v.get("occ") is not None]
        maxs = [v["maxRev"] for v in xs if v.get("maxRev") is not None]
        comps = [v["dongCnt"] for v in xs if v.get("dongCnt") is not None]
        out.append({
            "name": label, "n": len(xs),
            "comp": max(comps) if comps else len(xs),   # 경쟁(그 지역 삼삼 매물수)
            "net": round(statistics.mean(nets), 1) if nets else None,
            "occ": round(statistics.mean(occs), 1) if occs else None,
            "maxRev": round(statistics.mean(maxs), 1) if maxs else None,
        })
    out.sort(key=lambda r: (r["net"] is None, -(r["net"] or 0)))
    return out


def _rows_for(a):
    """공통 방 타입(rooms) 필터 — 순위·추천을 방 타입별로 볼 수 있게. 같은 동이라도 원룸/투룸/
    쓰리룸+ 예약률이 다르므로(예: 서초동 투룸 62.6% vs 원룸 53.0%)."""
    rm = a.get("rooms")
    return [x for x in P() if x.get("rooms") == rm] if rm else P()


def _group(field_or_key, rows=None):
    """rows(기본 P())를 동(시군구+동)/역 단위로 묶는다. dong은 구 다르면 분리(같은 동명 병합 방지)."""
    by = {}
    for x in (P() if rows is None else rows):
        if field_or_key == "dong":
            sg, dg = x.get("sigungu") or "", x.get("dong") or ""
            if not dg:
                continue
            by.setdefault(f"{sg} {dg}".strip(), []).append(x)
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


@app.route("/api/recommend")
def api_recommend():
    """신규진입 추천(블루오션): 수요(예약률) 있고 순수익 좋은데 경쟁 삼삼 매물이 적은 동/역/오피스텔.
    기회점수 = 평균순수익 × 평균예약률/100 ÷ √경쟁수 (수익·수요 높을수록↑, 경쟁 많을수록↓)."""
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

    def score(net, occ, comp):
        if net is None or occ is None or net <= 0:
            return None
        return round(net * (occ / 100) / max(comp, 1) ** 0.5, 1)

    def build(groups):
        out = []
        for r in _agg(groups):
            if r["n"] < min_n or r["occ"] is None or r["occ"] < min_occ:
                continue
            if max_comp is not None and r["comp"] > max_comp:
                continue
            sc = score(r["net"], r["occ"], r["comp"])
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
        if x.get("occ") is None or x["occ"] < min_occ or x.get("net") is None or x["net"] <= 0:
            continue
        comp = x.get("dongCnt") or 1
        if max_comp is not None and comp > max_comp:
            continue
        offices.append({
            "name": x.get("name") or "", "dong": f"{x.get('sigungu','')} {x.get('dong','')}".strip(),
            "station": x.get("station") or "", "pyeong": x.get("pyeong"),
            "comp": comp, "occ": x["occ"], "net": x["net"], "maxRev": x.get("maxRev"),
            "score": score(x["net"], x["occ"], comp),
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
