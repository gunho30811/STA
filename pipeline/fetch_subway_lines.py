# -*- coding: utf-8 -*-
"""카카오 로컬 API(SW8 지하철역 카테고리)로 '역 ↔ 호선' 표를 만든다.

data/subway_stations.csv 에는 역 좌표만 있어서 "2호선 전체" 같은 노선 단위 검색이
불가능했다. 카카오 SW8 문서는 place_name('강남역 2호선')과 category_name
('교통,수송 > 지하철,전철 > 수도권2호선')에 호선이 들어 있어, 이걸 긁어
data/subway_lines.csv (station,line) 로 저장한다. 환승역은 호선 수만큼 행이 생긴다.

인증은 fetch_kakao_poi.py 와 같다 — 맵 앱 JavaScript 키 + KA 헤더.

  python pipeline/fetch_subway_lines.py --dry   # 수집만(파일 안 건드림)
  python pipeline/fetch_subway_lines.py         # data/subway_lines.csv 갱신
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
sys.path.insert(0, os.path.join(BASE, "common"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
load_dotenv(os.path.join(BASE, ".env"))

import subway  # noqa: E402  # 역 좌표표(data/subway_stations.csv)와 이름을 맞춘다

OUT = os.path.join(BASE, "data", "subway_lines.csv")
ORIGIN = os.environ.get("KAKAO_LOCAL_ORIGIN", "https://rendits.duckdns.org")
API = "https://dapi.kakao.com/v2/local/search/category.json"

# 크롤·노출 대상 지역을 덮는 사각형(경도min, 위도min, 경도max, 위도max).
BOXES = {
    "수도권": (126.35, 36.95, 127.60, 38.30),
    "부산": (128.75, 34.95, 129.35, 35.45),
    "천안": (127.00, 36.60, 127.50, 37.05),
}


def _headers():
    key = os.environ.get("KAKAO_MAP_CLIENT_ID")
    if not key:
        raise SystemExit("KAKAO_MAP_CLIENT_ID 없음 (.env)")
    return {"Authorization": f"KakaoAK {key}",
            "KA": f"sdk/1.0.0 os/javascript lang/ko-KR device/Win32 origin/{ORIGIN}"}


def _search(bbox, depth=0):
    """SW8 카테고리 검색 — 한 사각형이 45건(3페이지)을 넘으면 4등분해 재귀로 훑는다."""
    x1, y1, x2, y2 = bbox
    h = _headers()
    p = {"category_group_code": "SW8", "rect": f"{x1},{y1},{x2},{y2}", "size": 15, "page": 1}
    r = requests.get(API, params=p, headers=h, timeout=25)
    r.raise_for_status()
    d = r.json()
    if d["meta"]["total_count"] > 45 and depth < 7:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        out = []
        for sub in ((x1, y1, mx, my), (mx, y1, x2, my), (x1, my, mx, y2), (mx, my, x2, y2)):
            out += _search(sub, depth + 1)
        return out
    out = list(d["documents"])
    while not d["meta"]["is_end"] and p["page"] < 3:
        p["page"] += 1
        time.sleep(0.12)
        d = requests.get(API, params=p, headers=h, timeout=25).json()
        out += d["documents"]
    time.sleep(0.12)
    return out


def _line_of(doc):
    """category_name 끝마디에서 호선명. '수도권2호선'→'2호선', '부산1호선'은 그대로
    (수도권 1호선과 이름이 겹치면 안 되므로 '수도권'만 떼어낸다)."""
    tail = (doc.get("category_name") or "").split(">")[-1].strip()
    # 끝마디가 노선명이 아니라 카테고리 그대로인 문서(잡음)는 버린다.
    if not tail or tail in ("지하철,전철", "교통,수송"):
        return None
    return re.sub(r"^수도권", "", tail).strip() or None


def _station_of(doc, line):
    """place_name('강남역 2호선')에서 역명만 — 뒤에 붙은 노선명을 떼고 '역'을 붙인다."""
    name = (doc.get("place_name") or "").strip()
    for suffix in (line, "수도권" + line):        # '강남역 2호선' / '강남역 수도권2호선'
        if name.endswith(" " + suffix):
            name = name[: -len(suffix) - 1].strip()
            break
    else:
        name = re.sub(r"\s+\S+$", "", name).strip() if " " in name else name
    if not name:
        return None
    return name if name.endswith("역") else name + "역"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="파일 저장 없이 결과만 출력")
    args = ap.parse_args()

    rows = {}                      # (역명, 호선) → (lat, lng)
    for region, box in BOXES.items():
        docs = _search(box)
        n0 = len(rows)
        for d in docs:
            line = _line_of(d)
            stn = _station_of(d, line) if line else None
            if not line or not stn:
                continue
            try:
                rows[(stn, line)] = (float(d["y"]), float(d["x"]))
            except (KeyError, TypeError, ValueError):
                continue
        print(f"{region}: 문서 {len(docs)}건 → 역·호선 조합 +{len(rows) - n0}", flush=True)

    lines = {}
    for stn, line in rows:
        lines.setdefault(line, set()).add(stn)
    new = {s for s, _ in rows} - subway.station_names()
    print(f"\n총 {len(rows)}조합 / {len(lines)}개 노선 / 역 {len({s for s, _ in rows})}개"
          f" (기존 좌표표에 없던 역 {len(new)}개는 이 파일의 좌표로 검색)")
    for line in sorted(lines, key=lambda k: (-len(lines[k]), k)):
        print(f"  {line}: {len(lines[line])}역")
    if new:
        print("새 역: " + ", ".join(sorted(new)[:12]) + ("…" if len(new) > 12 else ""))

    if args.dry:
        return
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["station", "line", "lat", "lon"])
        for stn, line in sorted(rows):
            lat, lon = rows[(stn, line)]
            w.writerow([stn, line, f"{lat:.6f}", f"{lon:.6f}"])
    print(f"\n저장: {OUT}")


if __name__ == "__main__":
    main()
