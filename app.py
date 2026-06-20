# -*- coding: utf-8 -*-
"""네이버 오피스텔 월세 매물 로컬 검색 웹앱 (Flask)."""
from flask import Flask, jsonify, request, render_template
import db

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/regions")
def api_regions():
    """{sido: {sigungu: [dong,...]}} 형태로 지역 트리 반환."""
    conn = db.connect()
    rows = conn.execute(
        "SELECT DISTINCT sido,sigungu,dong FROM regions ORDER BY sido,sigungu,dong"
    ).fetchall()
    conn.close()
    tree = {}
    for r in rows:
        tree.setdefault(r["sido"], {}).setdefault(r["sigungu"], []).append(r["dong"])
    return jsonify(tree)


@app.route("/api/stats")
def api_stats():
    conn = db.connect()
    total = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    by_sido = [dict(r) for r in conn.execute(
        "SELECT sido, COUNT(*) c FROM listings GROUP BY sido ORDER BY c DESC")]
    conn.close()
    return jsonify({"total": total, "by_sido": by_sido})


@app.route("/api/listings")
def api_listings():
    a = request.args
    where, params = [], []

    def eq(col, key):
        v = a.get(key)
        if v:
            where.append(f"{col}=?")
            params.append(v)

    eq("sido", "sido")
    eq("sigungu", "sigungu")

    dongs = [d for d in a.get("dong", "").split(",") if d]
    if dongs:
        where.append(f"dong IN ({','.join('?' * len(dongs))})")
        params += dongs

    dirs = [d for d in a.get("direction", "").split(",") if d]
    if dirs:
        where.append(f"direction IN ({','.join('?' * len(dirs))})")
        params += dirs

    def rng(col, lo, hi):
        if a.get(lo):
            where.append(f"{col} >= ?"); params.append(int(a.get(lo)))
        if a.get(hi):
            where.append(f"{col} <= ?"); params.append(int(a.get(hi)))

    rng("deposit", "deposit_min", "deposit_max")
    rng("rent", "rent_min", "rent_max")
    rng("area_real_m2", "area_min", "area_max")

    kw = a.get("keyword", "").strip()
    if kw:
        where.append("(articleName LIKE ? OR buildingName LIKE ? OR featureDesc LIKE ? OR tags LIKE ?)")
        params += [f"%{kw}%"] * 4

    sql_where = (" WHERE " + " AND ".join(where)) if where else ""

    sort_map = {
        "recent": "confirmYmd DESC",
        "rent_asc": "rent ASC, deposit ASC",
        "rent_desc": "rent DESC",
        "deposit_asc": "deposit ASC, rent ASC",
        "deposit_desc": "deposit DESC",
        "area_desc": "area_real_m2 DESC",
        "area_asc": "area_real_m2 ASC",
    }
    order = sort_map.get(a.get("sort", "recent"), "confirmYmd DESC")

    page = max(1, int(a.get("page", 1)))
    size = min(200, max(1, int(a.get("size", 30))))
    offset = (page - 1) * size

    conn = db.connect()
    total = conn.execute(f"SELECT COUNT(*) FROM listings{sql_where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM listings{sql_where} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [size, offset]).fetchall()
    conn.close()
    return jsonify({
        "total": total, "page": page, "size": size,
        "pages": (total + size - 1) // size,
        "items": [dict(r) for r in rows],
    })


if __name__ == "__main__":
    import socket
    db.init_db()
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        ip = "127.0.0.1"
    print(f"로컬:   http://127.0.0.1:5000")
    print(f"같은망: http://{ip}:5000  (다른 기기에서 이 주소로 접속)")
    app.run(host="0.0.0.0", port=5000, debug=False)
