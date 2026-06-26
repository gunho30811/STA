# -*- coding: utf-8 -*-
"""
주간 예약률 스냅샷: 현재 samsam_listings 를 지역(시도·시군구·동)×건물유형으로 집계해
samsam_snapshots 에 1행씩 적재한다. 매주 크롤(crawler.py) 직후 한 번 실행.

이렇게 매주 쌓으면 "인기가 오르는/내리는 지역"을 주·월 단위로 추적할 수 있다.

예약률 정의(수집일 기준 향후 30일):
  avg_occ_1m = 평균( booked_days_1m / (31 − blocked_days_1m) )   ← 31일 윈도우(오늘~+30 포함)
  avg_occ_3m = 평균( booked_days_3m / 91 )                        ← 향후 90일(막힘 미반영)

사용:
  python pipeline/samsam/snapshot.py                 # 오늘 날짜로 스냅샷
  python pipeline/samsam/snapshot.py --date 2026-06-26
"""
import argparse
import datetime as dt
import os
import statistics
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import db

COLS = ["snapshot_date", "sido", "sigungu", "dong", "building_type",
        "n", "avg_occ_1m", "avg_occ_3m", "avg_week"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat(),
                    help="스냅샷 날짜(YYYY-MM-DD), 기본 오늘")
    args = ap.parse_args()

    db.init_db()
    conn = db.connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT sido, sigungu, dong, building_type,"
        " booked_days_1m, blocked_days_1m, booked_days_3m, rent_total_weekly"
        " FROM samsam_listings"
    ).fetchall()]
    print(f"samsam_listings {len(rows)}건 → 스냅샷({args.date})")

    groups = {}
    for r in rows:
        key = (r.get("sido") or "", r.get("sigungu") or "", r.get("dong") or "",
               r.get("building_type") or "")
        bk1, bl1 = r.get("booked_days_1m") or 0, r.get("blocked_days_1m") or 0
        occ1 = min(1.0, bk1 / max(31 - bl1, 1))
        occ3 = min(1.0, (r.get("booked_days_3m") or 0) / 91)
        wk = (r.get("rent_total_weekly") or 0) / 10000
        groups.setdefault(key, {"occ1": [], "occ3": [], "wk": []})
        groups[key]["occ1"].append(occ1)
        groups[key]["occ3"].append(occ3)
        groups[key]["wk"].append(wk)

    out = []
    for (sido, sigungu, dong, bt), g in groups.items():
        out.append([
            args.date, sido, sigungu, dong, bt, len(g["occ1"]),
            round(statistics.mean(g["occ1"]) * 100, 1),
            round(statistics.mean(g["occ3"]) * 100, 1),
            round(statistics.mean(g["wk"]), 1),
        ])

    ph = ", ".join(["%s"] * len(COLS))
    upd = ", ".join(f"{c}=EXCLUDED.{c}" for c in COLS[5:])
    sql = (f"INSERT INTO samsam_snapshots ({', '.join(COLS)}) VALUES ({ph}) "
           f"ON CONFLICT (snapshot_date, sido, sigungu, dong, building_type) DO UPDATE SET {upd}")
    conn.executemany(sql, out)
    conn.commit()
    conn.close()
    print(f"스냅샷 {len(out)}개 지역×유형 적재 완료 ({args.date})")


if __name__ == "__main__":
    main()
