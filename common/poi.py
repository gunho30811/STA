# -*- coding: utf-8 -*-
"""수요시설 POI(병원·대학·산업단지) 로더 — 단기임대 수요의 '증거물품'.

왜 필요한가: 삼삼 매물이 없는 동네는 예약률 데이터 자체가 없어 수요를 알 수 없다.
그래서 "왜 이 동네에 단기임대 수요가 있을 것인가"를 설명하는 외부 근거가 필요하다.
  - hospital   상급종합·대형병원 → 통원·간병 가족의 주단위 체류 수요
  - university 대학 캠퍼스        → 계절학기·인턴·교환학생 수요
  - industrial 산업단지·테크노밸리 → 출장·파견 인력 수요
  - academy    학원가             → 재수·시험준비·방학 단기 체류(기러기 학부모 포함)
  - transport  KTX역·공항·터미널   → 지방/해외 유입 관문(단기 체류 수요의 길목)

데이터: data/poi.csv (kind,name,lat,lon). subway_stations.csv 와 같은 정적 CSV 패턴.

사용:
  from poi import load_poi, nearby
  nearby(37.5, 127.03, 2.0)   # 반경 2km 내 [{kind,name,dist_m}, ...] 가까운 순
"""
import csv
import os
from math import asin, cos, radians, sin, sqrt

_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "poi.csv")
_POI = None

KIND_KO = {"hospital": "병원", "university": "대학", "industrial": "산단",
           "academy": "학원가", "transport": "교통"}
KIND_ICON = {"hospital": "🏥", "university": "🎓", "industrial": "🏭",
             "academy": "📚", "transport": "🚄"}


def load_poi():
    """[(kind, name, lat, lon), ...] — 프로세스당 1회 로드."""
    global _POI
    if _POI is None:
        try:
            with open(_DATA, encoding="utf-8") as f:
                _POI = [(r["kind"], r["name"], float(r["lat"]), float(r["lon"]))
                        for r in csv.DictReader(f)]
        except Exception:
            _POI = []
    return _POI


def _haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = radians(lat1), radians(lat2)
    dphi, dl = radians(lat2 - lat1), radians(lon2 - lon1)
    h = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * 6371000 * asin(sqrt(h))


def nearby(lat, lng, km=2.0, limit=3):
    """좌표 반경 km 내 수요시설 — 가까운 순 [{kind,name,dist_m}]."""
    if lat is None or lng is None:
        return []
    out = []
    for kind, name, y, x in load_poi():
        d = _haversine_m(lat, lng, y, x)
        if d <= km * 1000:
            out.append({"kind": kind, "name": name, "dist_m": int(d)})
    out.sort(key=lambda p: p["dist_m"])
    return out[:limit]


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"POI {len(load_poi())}곳 로드: {_DATA}")
    print("역삼동 근처 2km:", nearby(37.5006, 127.0364, 2.0))
