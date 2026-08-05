# -*- coding: utf-8 -*-
"""카카오 로컬 API로 지역 확장분의 지하철역·수요시설 POI를 수집한다.

수도권 밖으로 크롤 지역을 넓히면(부산·천안) 역 좌표(data/subway_stations.csv)와
수요 근거 POI(data/poi.csv)가 없어서 ① 삼삼 매물의 역세권 정보가 비고 ② 공급부족 스팟
점수가 과소평가된다. 병원은 심평원 API(fetch_poi_hospital), 종사자수는 SGIS
(fetch_sgis_workers)로 받고, 나머지(지하철역·대학·교통거점·산단·관광지)를 여기서 받는다.

인증: 맵 앱의 **JavaScript 키**(KAKAO_MAP_CLIENT_ID) + KA 헤더(등록된 origin).
      REST 키가 아니어서 KA 헤더가 없으면 401. 로그인/메시지 앱 키는 로컬 서비스가 꺼져 있음.

  python pipeline/fetch_kakao_poi.py --dry        # 수집만 하고 파일은 안 건드림
  python pipeline/fetch_kakao_poi.py              # subway_stations.csv + poi.csv 갱신

재실행해도 중복이 쌓이지 않게, 각 지역 bbox 안의 기존 행을 지우고 새로 넣는다.
"""
import argparse
import csv
import os
import re
import sys
import time

import requests
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
load_dotenv(os.path.join(BASE, ".env"))

POI_CSV = os.path.join(BASE, "data", "poi.csv")
SUBWAY_CSV = os.path.join(BASE, "data", "subway_stations.csv")
ORIGIN = os.environ.get("KAKAO_LOCAL_ORIGIN", "https://rendits.duckdns.org")

# 지역별 검색 범위(경도min, 위도min, 경도max, 위도max)와 주소 검증 문자열
REGIONS = {
    "부산": {"bbox": (128.75, 34.95, 129.33, 35.42), "addr": "부산"},
    "천안": {"bbox": (127.05, 36.63, 127.45, 36.99), "addr": "천안"},
}

# 교통 거점은 이름을 콕 집어 검색한다(키워드 '역'은 잡음이 너무 많음).
TRANSPORT = {
    "부산": ["부산역", "부전역", "구포역", "김해국제공항",
             "부산종합버스터미널", "부산서부시외버스터미널"],
    "천안": ["천안아산역", "천안역", "천안종합버스터미널"],
}
# 관광 밀집지 — 단기숙박 수요 근거(게스트하우스형)
TOUR = {
    "부산": ["해운대해수욕장", "광안리해수욕장", "남포동", "서면", "송정해수욕장"],
    "천안": ["독립기념관"],
}
# 카카오 키워드 검색은 캠퍼스 건물·상호까지 다 준다('동아대 주차장', '와플대학'…).
# 그래서 "○○대학교" 또는 "○○대학교 △△캠퍼스" 형태만 남긴다.
UNIV_RE = re.compile(r"^[가-힣A-Za-z]+대학교( [가-힣A-Za-z]+캠퍼스)?$")
IND_RE = re.compile(r"^[가-힣A-Za-z0-9·\-]+(산업단지|테크노밸리|테크노파크|산단)$")
IND_KEYWORDS = ["산업단지", "테크노밸리", "일반산업단지"]

API = "https://dapi.kakao.com/v2/local/search"


def _headers():
    key = os.environ.get("KAKAO_MAP_CLIENT_ID")
    if not key:
        raise SystemExit("KAKAO_MAP_CLIENT_ID 없음 (.env)")
    return {"Authorization": f"KakaoAK {key}",
            "KA": f"sdk/1.0.0 os/javascript lang/ko-KR device/Win32 origin/{ORIGIN}"}


def _search(kind, params, bbox, depth=0):
    """카카오 로컬 검색 — 한 rect가 45건(3페이지)을 넘으면 4등분해 재귀로 훑는다."""
    x1, y1, x2, y2 = bbox
    out, h = [], _headers()
    p = dict(params, rect=f"{x1},{y1},{x2},{y2}", size=15, page=1)
    r = requests.get(f"{API}/{kind}.json", params=p, headers=h, timeout=25)
    r.raise_for_status()
    d = r.json()
    total = d["meta"]["total_count"]
    if total > 45 and depth < 4:                 # 잘림 방지: 사분할
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        for sub in ((x1, y1, mx, my), (mx, y1, x2, my), (x1, my, mx, y2), (mx, my, x2, y2)):
            out += _search(kind, params, sub, depth + 1)
        return out
    out += d["documents"]
    while not d["meta"]["is_end"] and p["page"] < 3:
        p["page"] += 1
        time.sleep(0.12)
        d = requests.get(f"{API}/{kind}.json", params=p, headers=h, timeout=25).json()
        out += d["documents"]
    time.sleep(0.12)
    return out


def _in_region(doc, region):
    return region["addr"] in (doc.get("address_name") or doc.get("road_address_name") or "")


def _dedup(items):
    """(name, lat, lon) 중복 제거 — 이름 기준, 먼저 나온 것 유지."""
    seen, out = set(), []
    for nm, y, x in items:
        if nm in seen:
            continue
        seen.add(nm)
        out.append((nm, round(float(y), 6), round(float(x), 6)))
    return out


def collect_subway(region):
    docs = _search("category", {"category_group_code": "SW8"}, region["bbox"])
    items = []
    for d in docs:
        if not _in_region(d, region):
            continue
        nm = (d["place_name"] or "").split()[0]      # '서면역 부산1호선' → '서면역'
        if nm.endswith("역") and len(nm) > 1:
            nm = nm[:-1]                             # 파일 관례: 역 접미사 없음
        if nm:
            items.append((nm, d["y"], d["x"]))
    return _dedup(items)


def collect_poi(region_name, region):
    """[(kind, name, lat, lon)] — 대학·교통·산단·관광."""
    out = []
    for d in _search("keyword", {"query": "대학교"}, region["bbox"]):
        nm = (d["place_name"] or "").split("(")[0].strip()
        if _in_region(d, region) and UNIV_RE.match(nm):
            out.append(("university", nm, d["y"], d["x"]))
    for kw in IND_KEYWORDS:
        for d in _search("keyword", {"query": kw}, region["bbox"]):
            nm = (d["place_name"] or "").split("(")[0].strip()
            if _in_region(d, region) and IND_RE.match(nm):
                out.append(("industrial", nm, d["y"], d["x"]))
    for q in TRANSPORT.get(region_name, []):
        for d in _search("keyword", {"query": q}, region["bbox"])[:1]:
            out.append(("transport", q, d["y"], d["x"]))
    for q in TOUR.get(region_name, []):
        for d in _search("keyword", {"query": q}, region["bbox"])[:1]:
            out.append(("tour", q, d["y"], d["x"]))
    # 종류별로 이름 중복 제거
    res = []
    for kind in ("university", "industrial", "transport", "tour"):
        res += [(kind, nm, y, x)
                for nm, y, x in _dedup([(n, y, x) for k, n, y, x in out if k == kind])]
    return res


def _inside(lat, lon, bbox):
    x1, y1, x2, y2 = bbox
    return x1 <= lon <= x2 and y1 <= lat <= y2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    stations, pois = {}, {}
    for name, region in REGIONS.items():
        stations[name] = collect_subway(region)
        pois[name] = collect_poi(name, region)
        kinds = {}
        for k, *_ in pois[name]:
            kinds[k] = kinds.get(k, 0) + 1
        print(f"  {name}: 지하철역 {len(stations[name])}개, POI {len(pois[name])}개 {kinds}",
              flush=True)

    if args.dry:
        for name in REGIONS:
            print(f"  [{name}] 역 예시:", [s[0] for s in stations[name][:8]])
            print(f"  [{name}] POI 예시:", [(k, n) for k, n, *_ in pois[name][:8]])
        return

    # ── 지하철역 병합 ─────────────────────────────────────────────────────────
    with open(SUBWAY_CSV, encoding="utf-8") as f:
        rows = [(r["name"], float(r["lat"]), float(r["lon"])) for r in csv.DictReader(f)]
    for name, region in REGIONS.items():
        rows = [r for r in rows if not _inside(r[1], r[2], region["bbox"])]   # 재실행 대비
        rows += [(nm, y, x) for nm, y, x in stations[name]]
    with open(SUBWAY_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "lat", "lon"])
        for nm, y, x in rows:
            w.writerow([nm, y, x])
    print(f"[done] {SUBWAY_CSV} — {len(rows)}역", flush=True)

    # ── POI 병합(지역 bbox 안의 같은 kind 행만 교체, 병원은 별도 스크립트라 보존) ──
    with open(POI_CSV, encoding="utf-8") as f:
        prev = [(r["kind"], r["name"], float(r["lat"]), float(r["lon"]))
                for r in csv.DictReader(f)]
    new_kinds = {"university", "industrial", "transport", "tour"}
    keep = [p for p in prev
            if not (p[0] in new_kinds
                    and any(_inside(p[2], p[3], rg["bbox"]) for rg in REGIONS.values()))]
    added = [p for name in REGIONS for p in pois[name]]
    with open(POI_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kind", "name", "lat", "lon"])
        for kind, nm, y, x in keep + [(k, n, float(y), float(x)) for k, n, y, x in added]:
            w.writerow([kind, nm, y, x])
    print(f"[done] {POI_CSV} — 기존 {len(keep)} + 신규 {len(added)}", flush=True)


if __name__ == "__main__":
    main()
