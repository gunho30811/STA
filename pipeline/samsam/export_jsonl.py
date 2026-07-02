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
    # ORDER BY로 줄 순서를 고정한다. 정렬이 없으면 export마다 행 순서가 뒤섞여 10MB 파일 전체가
    # "바뀐 것"이 되어 git 히스토리가 통째로 불어난다. 순서를 고정하면 커밋 사이엔 바뀐 예약률
    # 몇 줄만 diff → git 델타 압축으로 커밋당 증가분이 KB 수준 → 자주 커밋해도 repo가 거의 안 큰다.
    c = db.connect()
    dump(c.execute(f"SELECT {COLS} FROM samsam_listings ORDER BY room_id").fetchall(),
         os.path.join(LAB, "samsam_listings.jsonl"))
    dump(c.execute("SELECT snapshot_date,sido,sigungu,dong,building_type,n,avg_occ_1m,avg_occ_3m,avg_week "
                   "FROM samsam_snapshots "
                   "ORDER BY snapshot_date,sido,sigungu,dong,building_type").fetchall(),
         os.path.join(LAB, "samsam_snapshots.jsonl"))
    c.close()

if __name__ == "__main__":
    main()
