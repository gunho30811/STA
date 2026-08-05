# -*- coding: utf-8 -*-
"""SGIS 행정동 경계 → web/dong_geo.json 갱신(지역 추가용).

지도(/map)의 동 경계 폴리곤은 원래 수도권만 있어서, 크롤 지역을 넓히면(부산·천안 등)
그 지역은 경계 색칠 없이 원 폴백으로만 보인다. 이 스크립트로 해당 지역 경계를 받아 넣는다.

  python pipeline/fetch_dong_geo.py               # REGIONS 전체(부산·천안) 갱신
  python pipeline/fetch_dong_geo.py --dry         # 파일은 안 건드리고 건수만

필요: .env 의 DATA_SGIS_KR_ID / DATA_SGIS_KR_KEY.
주의: SGIS 시도코드는 MOIS(법정동) 코드와 다르다(SGIS 26=울산, MOIS 26=부산).
      파일에 넣는 sido 값은 기존 데이터와 맞춰 MOIS 코드를 쓴다.
좌표는 EPSG:5179(UTM-K)로 오므로 WGS84로 역변환하고, 파일이 커지지 않게
Douglas-Peucker로 단순화한다(기존 수도권 데이터도 동 평균 13점 수준).
"""
import argparse
import json
import os
import sys
from math import atan, cos, degrees, radians, sin, sqrt, tan

import requests
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
load_dotenv(os.path.join(BASE, ".env"))

OUT = os.path.join(BASE, "web", "dong_geo.json")
SGIS = "https://sgisapi.kostat.go.kr/OpenAPI3"
YEAR = "2022"
TOLERANCE = 0.0008        # 약 80m — 기존 수도권 폴리곤과 비슷한 거칠기
MAX_POINTS = 80

# (SGIS 시도코드, 파일에 쓸 MOIS 시도코드, 시군구 이름 필터(없으면 시도 전체))
REGIONS = [
    ("21", "26", None),        # 부산광역시 전역
    ("34", "44", "천안시"),     # 충청남도 중 천안시만
]

# ── EPSG:5179 → WGS84 (Snyder 횡축메르카토르 역변환, GRS80) ────────────────────
_A = 6378137.0
_F = 1 / 298.257222101
_E2 = _F * (2 - _F)
_K0, _FE, _FN = 0.9996, 1000000.0, 2000000.0
_LON0, _LAT0 = radians(127.5), radians(38.0)


def _meridian(phi):
    e2, e4, e6 = _E2, _E2 ** 2, _E2 ** 3
    return _A * ((1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
                 - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * sin(2 * phi)
                 + (15 * e4 / 256 + 45 * e6 / 1024) * sin(4 * phi)
                 - (35 * e6 / 3072) * sin(6 * phi))


_M0 = _meridian(_LAT0)


def to_wgs84(x, y):
    m = _M0 + (y - _FN) / _K0
    e1 = (1 - sqrt(1 - _E2)) / (1 + sqrt(1 - _E2))
    mu = m / (_A * (1 - _E2 / 4 - 3 * _E2 ** 2 / 64 - 5 * _E2 ** 3 / 256))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * sin(4 * mu)
            + (151 * e1 ** 3 / 96) * sin(6 * mu)
            + (1097 * e1 ** 4 / 512) * sin(8 * mu))
    ep2 = _E2 / (1 - _E2)
    c1, t1 = ep2 * cos(phi1) ** 2, tan(phi1) ** 2
    n1 = _A / sqrt(1 - _E2 * sin(phi1) ** 2)
    r1 = _A * (1 - _E2) / (1 - _E2 * sin(phi1) ** 2) ** 1.5
    d = (x - _FE) / (n1 * _K0)
    lat = phi1 - (n1 * tan(phi1) / r1) * (
        d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720)
    lon = _LON0 + (d - (1 + 2 * t1 + c1) * d ** 3 / 6
                   + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120
                   ) / cos(phi1)
    return round(degrees(lon), 4), round(degrees(lat), 4)     # [lon, lat] 순서(GeoJSON)


# ── 단순화 ────────────────────────────────────────────────────────────────────
def _perp(p, a, b):
    (x, y), (x1, y1), (x2, y2) = p, a, b
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return sqrt((x - x1) ** 2 + (y - y1) ** 2)
    t = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    return sqrt((x - (x1 + t * dx)) ** 2 + (y - (y1 + t * dy)) ** 2)


def simplify(pts, tol):
    """Douglas-Peucker(재귀 대신 스택) — 링 좌표를 tol(도) 이내로 줄인다."""
    if len(pts) < 4:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        far, dmax = -1, tol
        for k in range(i + 1, j):
            d = _perp(pts[k], pts[i], pts[j])
            if d > dmax:
                far, dmax = k, d
        if far > 0:
            keep[far] = True
            stack.append((i, far))
            stack.append((far, j))
    return [p for p, k in zip(pts, keep) if k]


def _ring(coords):
    """가장 바깥 링만(구멍 제거) 좌표 변환 + 단순화."""
    pts = [to_wgs84(x, y) for x, y in coords]
    out = simplify(pts, TOLERANCE)
    tol = TOLERANCE
    while len(out) > MAX_POINTS:          # 지나치게 복잡한 동은 더 거칠게
        tol *= 1.6
        out = simplify(pts, tol)
    if out[0] != out[-1]:
        out.append(out[0])
    return [list(p) for p in out]


def convert(feature, mois, sido_nm):
    geom = feature["geometry"]
    nm = (feature["properties"].get("adm_nm") or "").split()
    if len(nm) < 2:
        return None
    dong = nm[-1]
    sgg = "".join(nm[1:-1])               # '천안시 서북구' → '천안시서북구' (기존 표기 규칙)
    if geom["type"] == "Polygon":
        rings = [_ring(geom["coordinates"][0])]
        gtype = "Polygon"
        gcoords = rings[0]
        gcoords = [gcoords]
    else:                                  # MultiPolygon — 가장 큰 조각만
        big = max(geom["coordinates"], key=lambda poly: len(poly[0]))
        gcoords = [_ring(big[0])]
        gtype = "Polygon"
    pts = gcoords[0]
    cx = round(sum(p[0] for p in pts) / len(pts), 4)
    cy = round(sum(p[1] for p in pts) / len(pts), 4)
    return {"type": "Feature",
            "geometry": {"type": gtype, "coordinates": gcoords},
            "properties": {"name": dong, "sgg": sgg, "sido": mois, "cx": cx, "cy": cy}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="파일 저장 없이 건수만")
    args = ap.parse_args()

    sid, key = os.environ.get("DATA_SGIS_KR_ID"), os.environ.get("DATA_SGIS_KR_KEY")
    if not sid or not key:
        raise SystemExit("DATA_SGIS_KR_ID / DATA_SGIS_KR_KEY 없음 (.env)")
    tok = requests.get(f"{SGIS}/auth/authentication.json",
                       params={"consumer_key": sid, "consumer_secret": key},
                       timeout=30).json()["result"]["accessToken"]

    with open(OUT, encoding="utf-8") as f:
        data = json.load(f)
    before = len(data["features"])

    added = {}
    for sgis_cd, mois, name_filter in REGIONS:
        r = requests.get(f"{SGIS}/boundary/hadmarea.geojson",
                         params={"accessToken": tok, "year": YEAR,
                                 "adm_cd": sgis_cd, "low_search": "2"}, timeout=180)
        feats = r.json().get("features") or []
        out = []
        for ft in feats:
            nm = ft["properties"].get("adm_nm") or ""
            if name_filter and name_filter not in nm:
                continue
            c = convert(ft, mois, nm.split()[0] if nm else "")
            if c:
                out.append(c)
        added[mois] = out
        label = f"{name_filter or '전역'}({mois})"
        print(f"  {label}: 원본 {len(feats)}개 → 대상 {len(out)}개 동", flush=True)

    # 같은 시도코드의 기존 피처는 교체(재실행해도 중복 안 쌓이게)
    codes = set(added)
    data["features"] = [f for f in data["features"]
                        if f["properties"].get("sido") not in codes]
    for mois in added:
        data["features"].extend(added[mois])

    pts = [len(f["geometry"]["coordinates"][0]) for f in data["features"]
           if f["geometry"]["type"] == "Polygon"]
    print(f"[요약] 피처 {before} → {len(data['features'])}개, 폴리곤 평균 {sum(pts)/len(pts):.1f}점",
          flush=True)
    if args.dry:
        print("[dry] 저장 생략", flush=True)
        return

    with open(OUT, "w", encoding="utf-8") as f:
        f.write('{"type":"FeatureCollection", "features": [\n')
        for i, ft in enumerate(data["features"]):
            f.write(json.dumps(ft, ensure_ascii=False, separators=(",", ":")))
            f.write(",\n" if i < len(data["features"]) - 1 else "\n")
        f.write("]}\n")
    print(f"[done] {OUT} ({os.path.getsize(OUT):,} bytes)", flush=True)


if __name__ == "__main__":
    main()
