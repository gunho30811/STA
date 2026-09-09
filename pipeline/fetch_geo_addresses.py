# -*- coding: utf-8 -*-
"""매물 좌표 → 주소(도로명·지번·건물명) 캐시 채우기 (카카오 coord2address).

목록 크롤(listings)에는 주소가 없어 카드에 '빌라 / 일반상가' 같은 이름만 떴다.
좌표는 전부 있으므로 좌표→주소로 바꿔 `geo_address` 에 캐시해두면, 뷰어가 그걸 읽어
도로명·지번을 보여준다(그래야 그 주소로 검색해볼 수 있다).

뷰어도 화면에 뜨는 매물은 즉석에서 채우지만(common/geocode.py), 카카오 로컬 API는
하루 10만 건 한도라 **최근 확인된 매물부터** 미리 채워두는 게 이 스크립트의 일.

  python pipeline/fetch_geo_addresses.py                  # 기본 예산(8만건)만큼 채움
  python pipeline/fetch_geo_addresses.py --budget 20000   # 예산 지정
  python pipeline/fetch_geo_addresses.py --stats          # 진행률만 확인
"""
import argparse
import os
import sys
import time

from dotenv import load_dotenv

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "common"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
load_dotenv(os.path.join(BASE, ".env"))

import db  # noqa: E402
import geocode  # noqa: E402
import target_regions  # noqa: E402

DEFAULT_BUDGET = 80000     # 하루 한도(10만) 안에서 여유를 둔 기본 예산
CHUNK = 200                # 한 번에 변환할 좌표 수(스레드 8개로 나눠 호출)


def _todo(conn, limit):
    """아직 캐시에 없는 좌표를 '최근 확인된 매물' 순으로. [(gkey, lat, lng)]"""
    where, params = target_regions.sql_where()
    rows = conn.execute(
        "SELECT lat, lon, max(confirmymd) FROM listings "
        f"WHERE {where} AND tradetype = '월세' AND lat IS NOT NULL AND lon IS NOT NULL "
        "GROUP BY lat, lon ORDER BY 3 DESC NULLS LAST LIMIT %s",
        params + [limit * 3]).fetchall()
    seen, out = set(), []
    for r in rows:
        k = geocode.key_of(r[0], r[1])
        if k and k not in seen:
            seen.add(k)
            out.append((k, r[0], r[1]))
    have = set()
    for i in range(0, len(out), 1000):        # 이미 캐시된 것 제외
        have |= set(geocode.get_cached(conn, [k for k, _, _ in out[i:i + 1000]]))
    return [c for c in out if c[0] not in have][:limit]


def stats(conn):
    total = conn.execute(
        "SELECT count(*) FROM (SELECT DISTINCT lat, lon FROM listings "
        "WHERE tradetype='월세' AND lat IS NOT NULL) s").fetchone()[0]
    done = conn.execute("SELECT count(*) FROM geo_address").fetchone()[0]
    print(f"좌표 {total:,}개 중 주소 캐시 {done:,}개 ({done / max(total, 1) * 100:.1f}%)")
    return total, done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="이번 실행에서 변환할 좌표 수")
    ap.add_argument("--stats", action="store_true", help="진행률만 출력")
    args = ap.parse_args()

    db.init_db()
    conn = db.connect()
    try:
        stats(conn)
        if args.stats:
            return
        todo = _todo(conn, args.budget)
        print(f"이번에 채울 좌표: {len(todo):,}개", flush=True)
        filled, t0 = 0, time.time()
        for i in range(0, len(todo), CHUNK):
            got = geocode.fill(conn, todo[i:i + CHUNK], budget=CHUNK)
            filled += len(got)
            if (i // CHUNK) % 10 == 0:
                rate = filled / max(time.time() - t0, 1)
                print(f"  {i + CHUNK:,}/{len(todo):,} · 성공 {filled:,} · {rate:.0f}건/초", flush=True)
        print(f"완료: {filled:,}개 주소 캐시 ({time.time() - t0:.0f}초)", flush=True)
        stats(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
