# -*- coding: utf-8 -*-
"""행정동별 종사자 수 로더 — 단기임대 출장 수요의 직접 지표.

data/dong_workers.csv (pipeline/fetch_sgis_workers.py 가 SGIS에서 수집).
동 이름으로 조회 — 종사자 많은데 단기임대 없으면 = 출장/파견 미개척 지역.

  from workers import worker_count
  worker_count("역삼동")   # -> 종사자 수(없으면 0)
"""
import csv
import os
import re

_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "dong_workers.csv")
_BY_DONG = None

# SGIS는 행정동('역삼1동','종로1·2·3·4가동'), 네이버 listings는 법정동('역삼동').
# 행정동명에서 숫자·중점·'가'·방위를 떼 법정동 키로 정규화하고, 같은 법정동에 걸친
# 여러 행정동 종사자수를 합산한다.  역삼1동+역삼2동 → 역삼동.
_STRIP = re.compile(r"(제?\d+(·\d+)*가?|[동서남북]|신|구)?동$")


def _norm(dong):
    d = (dong or "").strip()
    m = re.match(r"^(.*?)(제?[\d·]+)?가?동$", d)
    base = m.group(1) if m else d
    # '가회동'처럼 base 가 비면 원본 사용
    return (base or d) + "동"


def _load():
    global _BY_DONG
    if _BY_DONG is None:
        _BY_DONG = {}
        try:
            with open(_DATA, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    key = _norm(r.get("dong"))
                    w = int(r.get("tot_worker") or 0)
                    _BY_DONG[key] = _BY_DONG.get(key, 0) + w   # 같은 법정동 행정동 합산
        except Exception:
            _BY_DONG = {}
    return _BY_DONG


def worker_count(dong):
    return _load().get(_norm(dong), 0)


_PTS = None


def _load_pts():
    """좌표 기반 조회용 [(lat, lon, tot_worker)] — 행정동↔법정동 이름 불일치 회피."""
    global _PTS
    if _PTS is None:
        _PTS = []
        try:
            with open(_DATA, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    try:
                        la, lo = float(r["lat"]), float(r["lon"])
                    except (KeyError, ValueError):
                        continue
                    _PTS.append((la, lo, int(r.get("tot_worker") or 0)))
        except Exception:
            _PTS = []
    return _PTS


_GRID = None


def _grid():
    """0.02도(~2km) 격자 → 종사자 포인트 인덱스. 반경 조회 시 인접 격자만 스캔."""
    global _GRID
    if _GRID is None:
        _GRID = {}
        for la, lo, w in _load_pts():
            _GRID.setdefault((round(la / 0.02), round(lo / 0.02)), []).append((la, lo, w))
    return _GRID


def workers_near(lat, lng, km=1.2):
    """좌표 반경 km 내 행정동 종사자수 합 — 이름 매칭 실패 대비(수원 구천동 등).
    행정동 중심이 반경 안에 든 것들의 종사자수 합산(그 지점 주변 직장 밀도).
    격자 인덱스로 인접 9칸만 스캔(전체 1,183개 순회 회피)."""
    if lat is None or lng is None:
        return 0
    import math
    g = _grid()
    gy, gx = round(lat / 0.02), round(lng / 0.02)
    tot = 0
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            for la, lo, w in g.get((gy + dy, gx + dx), ()):
                if math.hypot((lat - la) * 111, (lng - lo) * 88.8) <= km:
                    tot += w
    return tot


def has_data():
    return bool(_load())
