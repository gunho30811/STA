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

app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"))

SAM_COLS = ("room_id", "url", "name", "building_type", "building_name",
            "sido", "sigungu", "dong", "area_pyeong", "rent_total_weekly",
            "booked_days_1m", "blocked_days_1m", "basic_options", "extra_options",
            "station_500m_names")

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
    avail = max(30 - blocked, 1)
    r["occ"] = min(1.0, booked / avail)        # 예약률
    r["vac"] = 1 - r["occ"]                     # 공실률
    r["sam_week_man"] = round((r.get("rent_total_weekly") or 0) / 10000, 1)
    st = _parse_list(r.get("station_500m_names"))
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
    rows = _load_db()
    src = "DB(samsam_listings)"
    if not rows:   # DB 미연결(None) 또는 아직 비어있음(0건) → 합성 샘플 프리뷰
        rows = _load_sample()
        src = f"샘플({os.path.basename(SAMPLE)}, 합성 프리뷰 — 크롤 후 DB로 자동 전환)"
    rows = [_enrich(r) for r in rows]
    print(f"[samsam_app] {len(rows)}건 로드 — 출처: {src}", flush=True)
    return rows, src


LISTINGS, SOURCE = load_listings()


def _filtered(a):
    rows = LISTINGS
    for key in ("sido", "sigungu", "dong", "building_type"):
        v = a.get(key)
        if v:
            rows = [r for r in rows if r.get(key) == v]
    return rows


def _grp(rows):
    n = len(rows)
    if not n:
        return {"n": 0, "occ": None, "vac": None, "week": None}
    return {
        "n": n,
        "occ": round(statistics.mean(r["occ"] for r in rows) * 100, 1),
        "vac": round(statistics.mean(r["vac"] for r in rows) * 100, 1),
        "week": round(statistics.mean(r["sam_week_man"] for r in rows), 1),
    }


@app.route("/")
def index():
    return render_template("samsam.html")


@app.route("/api/facets")
def api_facets():
    sidos = sorted({r["sido"] for r in LISTINGS if r.get("sido")})
    tree = {}
    for r in LISTINGS:
        tree.setdefault(r.get("sido", ""), {}).setdefault(r.get("sigungu", ""), set()).add(r.get("dong", ""))
    tree = {s: {g: sorted(d) for g, d in gg.items()} for s, gg in tree.items()}
    btypes = sorted({r["building_type"] for r in LISTINGS if r.get("building_type")})
    opts = [{"code": c, "name": ko(c)} for c in sorted({o for r in LISTINGS for o in r["options"]})]
    return jsonify({"sido": sidos, "tree": tree, "building_type": btypes,
                    "options": opts, "total": len(LISTINGS), "source": SOURCE})


@app.route("/api/analyze")
def api_analyze():
    """지역/유형으로 거른 집합의 전체 통계 + 옵션별 있음/없음 예약률 비교 표."""
    rows = _filtered(request.args)
    overall = _grp(rows)
    opts = sorted({o for r in rows for o in r["options"]})
    table = []
    for o in opts:
        have = [r for r in rows if o in r["options"]]
        none = [r for r in rows if o not in r["options"]]
        gh, gn = _grp(have), _grp(none)
        diff = (round(gh["occ"] - gn["occ"], 1)
                if gh["occ"] is not None and gn["occ"] is not None else None)
        table.append({"option": o, "name": ko(o), "have": gh, "none": gn, "diff": diff})
    # 예약률 차이(있음−없음) 큰 순 = 수요에 영향 큰 옵션. None은 뒤로.
    table.sort(key=lambda x: (x["diff"] is None, -(x["diff"] or 0)))
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


if __name__ == "__main__":
    import socket
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        ip = "127.0.0.1"
    print(f"출처: {SOURCE} / {len(LISTINGS)}건")
    print("로컬:   http://127.0.0.1:5003")
    print(f"같은망: http://{ip}:5003")
    app.run(host="0.0.0.0", port=5003, debug=False)
