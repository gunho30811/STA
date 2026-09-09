# -*- coding: utf-8 -*-
"""좌표 → 주소(도로명·지번·건물명) 변환 + 캐시.

네이버 목록 크롤(listings)은 주소를 안 준다. 주소는 상세 크롤(naver_listings)에만
있는데 커버리지가 12.7%뿐이라, 카드에 "1동 / 빌라" 같은 이름만 뜨고 검색해볼 주소가
없었다. 매물마다 좌표(lat/lon)는 전부 있으므로 카카오 좌표→주소(coord2address)로
채우고, 결과를 `geo_address` 테이블에 캐시한다.

- 캐시 키는 소수점 5자리 좌표(약 1m) — 같은 건물의 여러 매물이 한 번의 호출을 공유한다.
- 카카오 로컬 API는 하루 10만 건 한도라, 화면에 실제로 보이는 매물부터 채우고
  (뷰어가 페이지당 최대 MAX_ONDEMAND건) 나머지는 야간 배치로 warm-up 한다
  (pipeline/fetch_geo_addresses.py).

인증은 fetch_kakao_poi 와 같음 — 맵 앱 JavaScript 키 + KA 헤더.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor

import requests

API = "https://dapi.kakao.com/v2/local/geo/coord2address.json"
ORIGIN = os.environ.get("KAKAO_LOCAL_ORIGIN", "https://rendits.duckdns.org")
MAX_ONDEMAND = 30      # 목록 요청 1건이 즉석에서 채울 최대 좌표 수
WORKERS = 8


def key_of(lat, lng):
    """캐시 키 — 소수점 5자리(약 1m)로 뭉친 좌표."""
    if lat is None or lng is None:
        return None
    return f"{float(lat):.5f},{float(lng):.5f}"


def _headers():
    k = os.environ.get("KAKAO_MAP_CLIENT_ID")
    if not k:
        return None
    return {"Authorization": f"KakaoAK {k}",
            "KA": f"sdk/1.0.0 os/javascript lang/ko-KR device/Win32 origin/{ORIGIN}"}


def _call(lat, lng):
    """카카오 좌표→주소 1건. (도로명, 지번, 건물명) 또는 None."""
    h = _headers()
    if not h:
        return None
    try:
        r = requests.get(API, params={"x": lng, "y": lat}, headers=h, timeout=6)
        docs = r.json().get("documents") or []
    except Exception:
        return None
    if not docs:
        return None
    d = docs[0]
    road, jibun = d.get("road_address") or {}, d.get("address") or {}
    return (road.get("address_name") or "", jibun.get("address_name") or "",
            road.get("building_name") or "")


def get_cached(conn, keys):
    """캐시에 있는 주소만 {key: {road, jibun, building}} 로."""
    keys = [k for k in set(keys) if k]
    if not keys:
        return {}
    try:
        rows = conn.execute(
            "SELECT gkey, road_address, jibun_address, building_name FROM geo_address "
            "WHERE gkey = ANY(%s)", [keys]).fetchall()
    except Exception:
        return {}
    return {r[0]: {"road": r[1] or "", "jibun": r[2] or "", "building": r[3] or ""}
            for r in rows}


def fill(conn, coords, budget=MAX_ONDEMAND):
    """캐시에 없는 좌표를 카카오로 채운다. coords=[(key, lat, lng)]. 채운 dict 반환."""
    todo = list(coords)[:budget]
    if not todo:
        return {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        got = list(ex.map(lambda c: (c[0], _call(c[1], c[2])), todo))
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    rows = [(k, float(lat), float(lng), v[0], v[1], v[2], now)
            for (k, v), (_, lat, lng) in zip(got, todo) if v]
    if rows:
        try:
            conn.executemany(
                "INSERT INTO geo_address(gkey, lat, lng, road_address, jibun_address,"
                " building_name, updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (gkey) DO NOTHING", rows)
            conn.commit()
        except Exception as e:
            print(f"[geocode] 캐시 저장 실패: {repr(e)[:100]}", flush=True)
    return {k: {"road": v[0], "jibun": v[1], "building": v[2]} for k, v in got if v}


def attach(conn, items, lat_key="lat", lng_key="lng", ondemand=True):
    """매물 목록에 주소를 붙인다(캐시 우선, 없으면 즉석 변환).

    붙는 값: item['geo'] = {road, jibun, building}. 실패하면 키 자체가 없다."""
    pairs = [(key_of(x.get(lat_key), x.get(lng_key)), x) for x in items]
    cache = get_cached(conn, [k for k, _ in pairs])
    if ondemand:
        missing, seen = [], set()
        for k, x in pairs:
            if k and k not in cache and k not in seen:
                seen.add(k)
                missing.append((k, x.get(lat_key), x.get(lng_key)))
        if missing:
            cache.update(fill(conn, missing))
    for k, x in pairs:
        g = cache.get(k)
        if g:
            x["geo"] = g
    return items
