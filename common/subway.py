# -*- coding: utf-8 -*-
"""
좌표 → 최근접 지하철역 계산. (웹앱·크롤 공용 — 순수 지오 유틸)

네이버 상세 API는 지하철 '도보 N분'만 주고 역명/거리(m)는 안 준다(역명/미터는
신플랫폼 fin.land 의 로그인 전용 front-api 에만 있음). 그래서 매물 좌표(lat/lng)와
역 좌표 테이블(data/subway_stations.csv, 수도권 589역)로 직접 최근접역을 계산한다.

사용:
  from subway import nearest_station, stations_within
  nearest_station(37.50834, 127.038279)   # -> {'station':'언주역', 'distance_m':406}
  stations_within(lat, lng, 500)          # 반경 내 역명 리스트
"""
import csv
import os
from math import radians, sin, cos, asin, sqrt

# 이 파일은 common/ 아래 → 레포 루트는 한 단계 위. data/subway_stations.csv 참조.
_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "subway_stations.csv")
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
    """가장 가까운 역 1개. {'station': 역명(N역), 'distance_m': 정수 미터} (없으면 None)."""
    if lat is None or lng is None:
        return None
    best = min(((haversine_m(lat, lng, y, x), n) for n, y, x in _load()),
              default=None)
    if not best:
        return None
    d, name = best
    return {"station": f"{name}역", "distance_m": round(d)}


def stations_within(lat, lng, radius_m):
    """반경 radius_m 안의 역명(N역) 리스트(가까운 순)."""
    if lat is None or lng is None:
        return []
    hits = sorted((haversine_m(lat, lng, y, x), n) for n, y, x in _load())
    return [f"{n}역" for d, n in hits if d <= radius_m]


# ── 노선(호선) ─────────────────────────────────────────────────────────────
# data/subway_lines.csv (station,line) — pipeline/fetch_subway_lines.py 가 카카오
# SW8 카테고리에서 만든다. 환승역은 호선 수만큼 행이 있다. '2호선 전체' 같은
# 노선 단위 검색에 쓴다(파일이 없으면 조용히 빈 표 — 기존 역 검색은 그대로 동작).
_LINES_CSV = os.path.join(os.path.dirname(_DATA), "subway_lines.csv")
_LINES = None
_LINE_COORDS = None


def station_names():
    """역 좌표표에 있는 역명 집합 (표기는 '강남역' 처럼 '역' 포함)."""
    return {f"{n}역" for n, _, _ in _load()}


def _load_lines():
    global _LINES, _LINE_COORDS
    if _LINES is None:
        m, coords = {}, {}
        try:
            with open(_LINES_CSV, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    m.setdefault(r["line"], set()).add(r["station"])
                    try:
                        coords[r["station"]] = (float(r["lat"]), float(r["lon"]))
                    except (KeyError, TypeError, ValueError):
                        pass
        except FileNotFoundError:
            m, coords = {}, {}
        _LINES = {k: sorted(v) for k, v in m.items()}
        _LINE_COORDS = coords
    return _LINES, _LINE_COORDS


def line_map():
    """{호선명: [역명(N역), ...]} — 역 이름순."""
    return _load_lines()[0]


def line_station_coords():
    """노선표에 있는 역의 좌표 {역명: (lat, lng)}.

    노선표(카카오 SW8)가 기존 좌표표(data/subway_stations.csv)보다 넓다 —
    서해선·김포골드라인 등 뒤늦게 생긴 노선의 역은 여기에만 있다."""
    return _load_lines()[1]


def stations_of(line):
    """그 호선의 역명 리스트 (없는 호선이면 빈 리스트)."""
    return line_map().get(line, [])


def lines_of(station):
    """그 역이 속한 호선 리스트 (환승역이면 여러 개)."""
    return sorted(k for k, v in line_map().items() if station in v)


if __name__ == "__main__":
    import sys
    la, lo = (float(sys.argv[1]), float(sys.argv[2])) if len(sys.argv) > 2 else (37.50834, 127.038279)
    print("최근접역:", nearest_station(la, lo))
    print("500m 내:", stations_within(la, lo, 500))
    print("1km 내:", stations_within(la, lo, 1000))
