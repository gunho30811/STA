# -*- coding: utf-8 -*-
"""한국관광공사 TourAPI → data/poi.csv 의 tour 행 갱신 (관광 밀집지역).

관광지는 수도권에만 1,500곳이 넘어 개별로 찍으면 지도가 지저분하고 의미도 없다.
그래서 관광지 좌표를 ~1km 격자로 묶어 **밀집도가 높은 지역(핫스팟)만** POI 로 남긴다.
단기임대 수요 근거: 외국인·관광객이 몰리는 지역 = 게스트하우스/단기숙박 수요.

  python pipeline/fetch_poi_tour.py           # poi.csv 의 tour 행 교체
  python pipeline/fetch_poi_tour.py --dry     # 파일 안 쓰고 결과만
  python pipeline/fetch_poi_tour.py --min 6   # 격자당 관광지 최소 개수(기본 6)

필요: .env 의 DATA_GO_KR_KEY. serviceKey 는 Encoding 키를 URL 에 그대로 붙일 것.
"""
import argparse
import csv
import os
import sys
import time
from collections import defaultdict

import requests
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
load_dotenv(os.path.join(BASE, ".env"))

OUT = os.path.join(BASE, "data", "poi.csv")
API = "https://apis.data.go.kr/B551011/KorService2/areaBasedList2"
AREAS = {"1": "서울", "31": "경기", "2": "인천"}
CONTENT = "12"   # 관광지


def fetch_area(area_cd, key):
    """시도 관광지 전량 — [(title, lat, lon)]."""
    out, page = [], 1
    while True:
        url = (f"{API}?serviceKey={key}&MobileOS=ETC&MobileApp=rendit"
               f"&numOfRows=100&pageNo={page}&_type=json"
               f"&contentTypeId={CONTENT}&areaCode={area_cd}&arrange=A")
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        body = r.json()["response"]["body"]
        items = body.get("items")
        if not items or not items.get("item"):
            break
        for it in items["item"]:
            try:
                y, x = float(it["mapy"]), float(it["mapx"])
            except (KeyError, ValueError, TypeError):
                continue
            if 33 <= y <= 39.5 and 124 <= x <= 132:
                out.append((it.get("title", "").strip(), y, x))
        if page * 100 >= int(body.get("totalCount", 0)):
            break
        page += 1
        time.sleep(0.3)
    return out


# 대표 지명(핫스팟 라벨) — 격자 중심 최근접으로 이름 붙임. 없으면 '관광밀집지'.
LANDMARKS = [
    ("명동·남산", 37.5601, 126.9860), ("경복궁·인사동", 37.5760, 126.9830),
    ("홍대·연남", 37.5561, 126.9236), ("이태원", 37.5344, 126.9944),
    ("강남·삼성", 37.5088, 127.0630), ("잠실·롯데월드", 37.5111, 127.0980),
    ("여의도", 37.5230, 126.9245), ("성수·서울숲", 37.5445, 127.0440),
    ("동대문", 37.5665, 127.0090), ("종로·대학로", 37.5820, 127.0020),
    ("가로수길·압구정", 37.5270, 127.0230), ("북촌·삼청동", 37.5825, 126.9830),
    ("인천 차이나타운", 37.4750, 126.6180), ("송도", 37.3830, 126.6560),
    ("수원 화성", 37.2880, 127.0150), ("에버랜드·용인", 37.2940, 127.2020),
    ("파주 헤이리·임진각", 37.7900, 126.6900), ("가평·청평", 37.8300, 127.5100),
    ("일산 호수공원", 37.6560, 126.7700), ("분당·판교", 37.3900, 127.1100),
]


def label_for(lat, lng):
    best, bd = "관광밀집지", 9e9
    for nm, y, x in LANDMARKS:
        d = (lat - y) ** 2 + (lng - x) ** 2
        if d < bd:
            bd, best = d, nm
    # 5km(약 0.045도) 넘게 떨어지면 대표 지명 안 붙임
    return best if bd ** 0.5 <= 0.05 else "관광밀집지"


def cluster(points, min_count):
    """~2.2km 격자(0.02도)로 묶어 관광지 min_count 개 이상인 격자만 → [(name, lat, lon, n)]."""
    def snap(v):
        return round(v / 0.02) * 0.02
    grid = defaultdict(list)
    for _, y, x in points:
        grid[(snap(y), snap(x))].append((y, x))
    spots = []
    for (gy, gx), pts in grid.items():
        if len(pts) < min_count:
            continue
        cy = sum(p[0] for p in pts) / len(pts)
        cx = sum(p[1] for p in pts) / len(pts)
        spots.append((label_for(cy, cx), round(cy, 6), round(cx, 6), len(pts)))
    # 같은 라벨이 여러 격자에 걸치면 가장 밀집한 것 하나만
    best = {}
    for nm, y, x, n in spots:
        if nm == "관광밀집지":
            best[(y, x)] = (nm, y, x, n)   # 이름 없는 건 좌표별 유지
        elif nm not in best or n > best[nm][3]:
            best[nm] = (nm, y, x, n)
    return sorted(best.values(), key=lambda s: -s[3])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--min", type=int, default=8, help="격자당 관광지 최소 개수")
    args = ap.parse_args()

    key = os.environ.get("DATA_GO_KR_KEY")
    if not key:
        print("DATA_GO_KR_KEY 없음"); return

    allpts = []
    for cd, nm in AREAS.items():
        got = fetch_area(cd, key)
        allpts += got
        print(f"  {nm} 관광지: {len(got)}곳")
    print(f"관광지 총 {len(allpts)}곳 → 밀집 격자 집계")

    spots = cluster(allpts, args.min)
    rows = [("tour", nm, y, x) for nm, y, x, n in spots]
    print(f"관광 밀집지역 {len(rows)}곳:")
    for nm, y, x, n in spots[:20]:
        print(f"   {nm}: 관광지 {n}곳 ({y},{x})")

    if args.dry:
        return
    keep = []
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            keep = [r for r in csv.DictReader(f) if r["kind"] != "tour"]
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kind", "name", "lat", "lon"])
        for r in keep:
            w.writerow([r["kind"], r["name"], r["lat"], r["lon"]])
        for kind, nm, y, x in rows:
            w.writerow([kind, nm, y, x])
    print(f"저장: {OUT} (tour {len(rows)} + 기타 {len(keep)})")


if __name__ == "__main__":
    main()
