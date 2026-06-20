# -*- coding: utf-8 -*-
"""
삼삼엠투 × 네이버부동산 단기임대 수익성 뷰어.

핵심 질문: "1달 기준, 공실 반영 실현 순수익이 크고 보증금 작은(싼데 잘 파는) 매물이 뭐냐"
데이터: data/net_profit_integrated.csv (전국 엄격매칭, 만원 단위)
보조:   landlord_best_dong / vacancy_by_gu / by_station
"""
import csv
import os
from flask import Flask, jsonify, request, render_template

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"))


def _num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s == "" or s.startswith("미표기") or s.startswith("-"):
        # '-1' 같은 미표기 음수도 None 처리 (단, 순수익 음수는 별도 컬럼이라 영향X)
        if s in ("", "-"):
            return None
    try:
        f = float(s)
        return int(f) if f.is_integer() else round(f, 2)
    except ValueError:
        return None


# net_profit_integrated.csv 원본 컬럼 → 짧은 키
PROFIT_MAP = {
    "삼삼ID": "id", "매물명": "name", "건물유형": "btype", "방수": "rooms",
    "시도": "sido", "시군구": "sigungu", "동": "dong", "평수": "pyeong",
    "삼삼주당_만원": "wk", "삼삼월환산_만원": "mEq",
    "1달예약일": "bk", "1달막힘일": "blk", "1달실현수익_만원": "realized",
    "네이버월세_만원": "nRent", "네이버관리비_만원": "nMgmt", "관리비표기여부": "mgmtFlag",
    "네이버월총_만원": "nTotal", "네이버보증금_만원": "nDep", "매칭매물수": "matches",
    "네이버월총÷삼삼주당": "mult", "실현효율(1달실현÷네이버월총)": "eff",
    "현실효율(1달실현÷네이버월세)": "realEff", "순수익_만원(1달실현−월세−관리비)": "profit",
    "네이버건물": "bldg", "네이버링크": "naverUrl", "삼삼링크": "samUrl",
}
NUM_FIELDS = {"pyeong", "wk", "mEq", "bk", "blk", "realized", "nRent", "nMgmt",
              "nTotal", "nDep", "matches", "mult", "eff", "realEff", "profit",
              "guVacancy", "guCompetitors"}


def load_gu_vacancy():
    """시군구별 공실률(%)·경쟁 매물수 (data/vacancy_by_gu.csv, 시도+시군구로 매칭)."""
    out = {}
    path = os.path.join(DATA, "vacancy_by_gu.csv")
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            out[(r.get("시도"), r.get("시군구"))] = {
                "guVacancy": _num(r.get("공실률(%)")),
                "guCompetitors": _num(r.get("매물수")),
            }
    return out


GU_VACANCY = load_gu_vacancy()


def load_profit():
    rows = []
    path = os.path.join(DATA, "net_profit_integrated.csv")
    if not os.path.exists(path):                      # 통합본 없으면 구버전 폴백
        path = os.path.join(DATA, "net_profit_strict.csv")
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            o = {}
            for kr, key in PROFIT_MAP.items():
                v = r.get(kr)
                o[key] = _num(v) if key in NUM_FIELDS else (v or "")
            gu = GU_VACANCY.get((o["sido"], o["sigungu"]), {})
            o["guVacancy"] = gu.get("guVacancy")
            o["guCompetitors"] = gu.get("guCompetitors")
            rows.append(o)
    return rows


def load_csv(name):
    path = os.path.join(DATA, name)
    out = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            out.append({k: (_num(v) if _num(v) is not None else v) for k, v in r.items()})
    return out


PROFIT = load_profit()


@app.route("/")
def index():
    return render_template("profit.html")


@app.route("/api/profit")
def api_profit():
    a = request.args
    items = list(PROFIT)

    def fnum(key):
        v = a.get(key)
        return float(v) if v not in (None, "") else None

    sido = a.get("sido", "")
    if sido:
        items = [x for x in items if x.get("sido") == sido]
    btype = a.get("btype", "")
    if btype:
        items = [x for x in items if x.get("btype") == btype]
    rooms = a.get("rooms", "")
    if rooms:
        items = [x for x in items if x.get("rooms") == rooms]
    sigungu = a.get("sigungu", "")
    if sigungu:
        items = [x for x in items if x["sigungu"] == sigungu]
    dong = a.get("dong", "").strip()
    if dong:
        items = [x for x in items if dong in (x["dong"] or "")]
    kw = a.get("keyword", "").strip()
    if kw:
        items = [x for x in items if kw in (x["name"] or "") or kw in (x["bldg"] or "")]

    def ge(key, field):
        v = fnum(key)
        if v is not None:
            return [x for x in items if (x[field] is not None and x[field] >= v)]
        return items

    def le(key, field):
        v = fnum(key)
        if v is not None:
            return [x for x in items if (x[field] is not None and x[field] <= v)]
        return items

    if fnum("profit_min") is not None:
        items = ge("profit_min", "profit")
    if fnum("mult_min") is not None:
        items = ge("mult_min", "mult")
    if fnum("bk_min") is not None:
        items = ge("bk_min", "bk")
    if fnum("pyeong_min") is not None:
        items = ge("pyeong_min", "pyeong")
    if fnum("pyeong_max") is not None:
        items = le("pyeong_max", "pyeong")
    if fnum("dep_max") is not None:
        items = le("dep_max", "nDep")

    sort = a.get("sort", "profit")
    direction = a.get("dir", "desc")
    valid_keys = set(PROFIT_MAP.values()) | {"guVacancy", "guCompetitors"}
    key = sort if sort in valid_keys else "profit"
    is_num = key in NUM_FIELDS
    rev = direction == "desc"
    present = [x for x in items if x.get(key) not in (None, "")]
    missing = [x for x in items if x.get(key) in (None, "")]
    if is_num:
        present.sort(key=lambda x: x[key], reverse=rev)
    else:
        present.sort(key=lambda x: str(x[key]).lower(), reverse=rev)
    items = present + missing

    # 요약
    profits = [x["profit"] for x in items if x["profit"] is not None]
    mults = [x["mult"] for x in items if x["mult"] is not None]
    summary = {
        "count": len(items),
        "profit_med": _median(profits),
        "profit_max": max(profits) if profits else None,
        "mult_med": _median(mults),
    }
    page = max(1, int(a.get("page", 1)))
    size = min(300, int(a.get("size", 50)))
    total = len(items)
    items = items[(page - 1) * size: page * size]
    return jsonify({"summary": summary, "total": total, "page": page,
                    "size": size, "pages": (total + size - 1) // size, "items": items})


@app.route("/api/sigungu")
def api_sigungu():
    s = sorted({x["sigungu"] for x in PROFIT if x["sigungu"]})
    return jsonify(s)


@app.route("/api/facets")
def api_facets():
    def uniq(k):
        return sorted({x.get(k) for x in PROFIT if x.get(k)})
    return jsonify({
        "sido": uniq("sido"),
        "sigungu": uniq("sigungu"),
        "btype": uniq("btype"),
        "rooms": ["원룸", "투룸", "쓰리룸+"],
        "total": len(PROFIT),
    })


@app.route("/api/best_dong")
def api_best_dong():
    rows = load_csv("landlord_best_dong.csv")
    rows.sort(key=lambda x: x.get("이득지수(예약기준)") or 0, reverse=True)
    return jsonify(rows)


@app.route("/api/vacancy_gu")
def api_vacancy_gu():
    rows = load_csv("vacancy_by_gu.csv")
    rows.sort(key=lambda x: x.get("점유율(%)") or 0, reverse=True)
    return jsonify(rows)


@app.route("/api/by_station")
def api_by_station():
    rows = load_csv("by_station.csv")
    rows = [r for r in rows if (r.get("매물수") or 0) >= 3]  # 표본 3개 이상만
    rows.sort(key=lambda x: x.get("점유율(%)") or 0, reverse=True)
    return jsonify(rows)


def _median(xs):
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else round((xs[n // 2 - 1] + xs[n // 2]) / 2, 2)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"수익성 매물 {len(PROFIT)}건 로드됨")
    print(f"http://127.0.0.1:{port} 에서 실행")
    app.run(host="0.0.0.0", port=port, debug=False)
