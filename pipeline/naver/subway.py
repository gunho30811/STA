# -*- coding: utf-8 -*-
"""
좌표 → 최근접 지하철역 계산.

네이버 상세 API는 지하철 '도보 N분'만 주고 역명/거리(m)는 안 준다(역명/미터는
신플랫폼 fin.land 의 로그인 전용 front-api 에만 있음). 그래서 매물 좌표(lat/lng)와
역 좌표 테이블(data/subway_stations.csv, 수도권 589역)로 직접 최근접역을 계산한다.

사용:
  from subway import nearest_station, stations_within
  nearest_station(37.50834, 127.038279)   # -> {'station':'언주', 'distance_m':406}
  stations_within(lat, lng, 500)          # 반경 내 역명 리스트
"""
import csv
import os
from math import radians, sin, cos, asin, sqrt

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "subway_stations.csv")
_STATIONS = None


def _load():
    global _STATIONS
    if _STATIONS is None:
        with open(_DATA, encoding="utf-8") as f:
            _STATIONS = [(r["name"], float(r["lat"]), float(r["lon"]))
                         for r in csv.DictReader(f)]
    return _STATIONS


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = radians(lat1), radians(lat2)
    dphi, dl = radians(lat2 - lat1), radians(lon2 - lon1)
    h = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * 6371000 * asin(sqrt(h))


def nearest_station(lat, lng):
    """가장 가까운 역 1개. {'station': 역명, 'distance_m': 정수 미터} (없으면 None)."""
    if lat is None or lng is None:
        return None
    best = min(((haversine_m(lat, lng, y, x), n) for n, y, x in _load()),
              default=None)
    if not best:
        return None
    d, name = best
    return {"station": name, "distance_m": round(d)}


def stations_within(lat, lng, radius_m):
    """반경 radius_m 안의 역명 리스트(가까운 순)."""
    if lat is None or lng is None:
        return []
    hits = sorted((haversine_m(lat, lng, y, x), n) for n, y, x in _load())
    return [n for d, n in hits if d <= radius_m]


if __name__ == "__main__":
    import sys
    la, lo = (float(sys.argv[1]), float(sys.argv[2])) if len(sys.argv) > 2 else (37.50834, 127.038279)
    print("최근접역:", nearest_station(la, lo))
    print("500m 내:", stations_within(la, lo, 500))
    print("1km 내:", stations_within(la, lo, 1000))
