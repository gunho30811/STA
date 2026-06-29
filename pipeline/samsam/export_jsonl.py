# -*- coding: utf-8 -*-
"""samsam_listings + samsam_snapshots → lab/ jsonl 파일로 export.
배포(Vercel 미국 함수)에서 DB(서울) 왕복 없이 파일로 빠르게 읽기 위함. 주간 크롤 후 실행해 커밋.
  python pipeline/samsam/export_jsonl.py
"""
import json, os, sys
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import db

LAB = os.path.join(BASE, "lab")
COLS = ("room_id,url,name,building_type,building_name,sido,sigungu,dong,area_pyeong,"
        "rent_total_weekly,booked_days_1m,blocked_days_1m,basic_options,extra_options,"
        "station_500m_names,collected_at")

def dump(rows, path):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(dict(r), ensure_ascii=False, default=str) + "\n")
    print(f"  {len(rows)}건 → {path}")

def main():
    c = db.connect()
    dump(c.execute(f"SELECT {COLS} FROM samsam_listings").fetchall(),
         os.path.join(LAB, "samsam_listings.jsonl"))
    dump(c.execute("SELECT snapshot_date,sido,sigungu,dong,building_type,n,avg_occ_1m,avg_occ_3m,avg_week "
                   "FROM samsam_snapshots").fetchall(),
         os.path.join(LAB, "samsam_snapshots.jsonl"))
    c.close()

if __name__ == "__main__":
    main()
