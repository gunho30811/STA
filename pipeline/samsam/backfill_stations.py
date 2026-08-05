# -*- coding: utf-8 -*-
"""역 좌표 테이블이 늘어난 뒤, 이미 수집된 매물의 역세권 필드를 다시 계산한다.

station_500m_* / station_1km_* 은 수집 시점에 계산돼 DB에 박히므로, 나중에
data/subway_stations.csv 에 역을 추가해도(예: 2026-08-05 부산 117역) 기존 매물은
계속 비어 있다. 재크롤(--redo)은 예약조회 한도를 크게 쓰므로 좌표만으로 다시 계산한다.

  python pipeline/samsam/backfill_stations.py --bbox 128.75,34.95,129.33,35.42   # 부산
  python pipeline/samsam/backfill_stations.py --all                              # 전체
"""
import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "common"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import db  # noqa: E402
from subway import stations_within  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", default="", help="경도min,위도min,경도max,위도max")
    ap.add_argument("--all", action="store_true", help="좌표 있는 전체 매물")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    if not args.bbox and not args.all:
        raise SystemExit("--bbox 또는 --all 필요")

    conn = db.connect()
    q = ("SELECT room_id, lat, lng, station_500m_names FROM samsam_listings "
         "WHERE lat IS NOT NULL AND lng IS NOT NULL")
    params = []
    if args.bbox:
        x1, y1, x2, y2 = [float(v) for v in args.bbox.split(",")]
        q += " AND lng BETWEEN %s AND %s AND lat BETWEEN %s AND %s"
        params = [x1, x2, y1, y2]
    rows = conn.execute(q, params).fetchall()
    print(f"대상 {len(rows)}건", flush=True)

    changed = 0
    for rid, lat, lng, old500 in rows:
        s500 = stations_within(float(lat), float(lng), 500)
        s1k = stations_within(float(lat), float(lng), 1000)
        new = json.dumps(s500, ensure_ascii=False)
        if new == (old500 or "") and s500:
            continue
        changed += 1
        if args.dry:
            continue
        conn.execute(
            "UPDATE samsam_listings SET station_500m_count=%s, station_500m_names=%s,"
            " station_1km_count=%s, station_1km_names=%s WHERE room_id=%s",
            (len(s500), new, len(s1k), json.dumps(s1k, ensure_ascii=False), rid))
    if not args.dry:
        conn.commit()
    with_st = sum(1 for r in rows
                  if stations_within(float(r[1]), float(r[2]), 1000))
    print(f"{'[dry] ' if args.dry else ''}갱신 {changed}건 / 1km 내 역 있는 매물 {with_st}건",
          flush=True)
    conn.close()


if __name__ == "__main__":
    main()
