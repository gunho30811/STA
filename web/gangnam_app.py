# -*- coding: utf-8 -*-
"""
강남구 네이버부동산 매물 뷰어 (Flask).

lab/naver_listings_gangnam.jsonl (naver_listings 스키마, 강남 5타입 전수 10,363건)을
그대로 읽어 카드 그리드 + 상세 모달로 보여준다. DB 불필요(로컬 jsonl만 읽음).

    python web/gangnam_app.py        # http://127.0.0.1:5002
"""
import json
import os

from flask import Flask, jsonify, render_template, request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "lab", "naver_listings_gangnam.jsonl")
M2_PER_PYEONG = 3.305785

app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"))

TYPE_NAMES = {
    "APT": "아파트", "OPST": "오피스텔", "VL": "빌라",
    "OR": "원룸", "DDDGG": "단독/다가구", "SG": "상가",
}


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
            rows.append(r)
    return rows


LISTINGS = _load()


@app.route("/")
def index():
    return render_template("gangnam.html")


@app.route("/api/facets")
def api_facets():
    dongs = sorted({x.get("dong") for x in LISTINGS if x.get("dong")})
    types = [{"code": c, "name": TYPE_NAMES.get(c, c)}
             for c in ["APT", "OPST", "VL", "OR", "DDDGG", "SG"]
             if any(x.get("building_type_code") == c for x in LISTINGS)]
    return jsonify({"dongs": dongs, "types": types, "total": len(LISTINGS)})


@app.route("/api/stats")
def api_stats():
    by_type, by_dong, rents = {}, {}, []
    for x in LISTINGS:
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
        "total": len(LISTINGS),
        "dong_count": len(by_dong),
        "rent_median": med,
        "by_type": [{"code": k, "name": TYPE_NAMES.get(k, k), "count": v}
                    for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])],
    })


@app.route("/api/listings")
def api_listings():
    a = request.args
    items = LISTINGS

    types = [t for t in a.get("types", "").split(",") if t]
    if types:
        items = [x for x in items if x.get("building_type_code") in types]

    dongs = [d for d in a.get("dongs", "").split(",") if d]
    if dongs:
        items = [x for x in items if x.get("dong") in dongs]

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

    sort = a.get("sort", "recent")
    keyf = {
        "rent_asc": lambda x: (_n(x.get("rent_monthly")) is None, _n(x.get("rent_monthly")) or 0),
        "rent_desc": lambda x: -(_n(x.get("rent_monthly")) or 0),
        "deposit_asc": lambda x: (_n(x.get("deposit")) is None, _n(x.get("deposit")) or 0),
        "deposit_desc": lambda x: -(_n(x.get("deposit")) or 0),
        "area_desc": lambda x: -(_n(x.get("pyeong")) or 0),
        "area_asc": lambda x: (_n(x.get("pyeong")) is None, _n(x.get("pyeong")) or 0),
        "recent": lambda x: x.get("confirmed_at") or "",
    }.get(sort, lambda x: x.get("confirmed_at") or "")
    rev = sort in ("recent",)
    items = sorted(items, key=keyf, reverse=rev)

    total = len(items)
    page = max(1, int(a.get("page", 1)))
    size = min(120, max(1, int(a.get("size", 24))))
    start = (page - 1) * size
    return jsonify({
        "total": total, "page": page, "size": size,
        "pages": (total + size - 1) // size,
        "items": items[start:start + size],
    })


def _n(v):
    return v if isinstance(v, (int, float)) else None


if __name__ == "__main__":
    import socket
    print(f"강남 매물 {len(LISTINGS)}건 로드됨")
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        ip = "127.0.0.1"
    print("로컬:   http://127.0.0.1:5002")
    print(f"같은망: http://{ip}:5002")
    app.run(host="0.0.0.0", port=5002, debug=False)
