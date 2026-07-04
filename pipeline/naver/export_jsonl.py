# -*- coding: utf-8 -*-
"""naver_listings(수도권) → lab/naver_listings.jsonl.

부동산 매물 뷰어(web/gangnam_app.py)가 DB 왕복 없이 파일로 빠르게 읽기 위함.
naver_listings 전 컬럼을 그대로 덤프한다(뷰어 상세 모달이 대부분 필드를 쓰므로). 주간 크롤 후 실행해 커밋.
  python pipeline/naver/export_jsonl.py
"""
import json, os, sys
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import db

OUT = os.path.join(BASE, "lab", "naver_listings.jsonl")

# 뷰어가 실제 쓰는 컬럼만. 수도권 전 유형 115k라 파일이 커져(전컬럼 100MB+, GitHub 100MB 한도 초과),
# 무거운·미사용 컬럼을 뺀다: url(article_no로 재구성), tags/lat/lng/road_address/agent_office/
# floor_total/bathrooms/subway_walk/building_type(코드와 중복). → ~70MB 수준.
COLS = (
    "article_no,building_name,building_type_code,"
    "sido,sigungu,dong,deposit,rent_monthly,maintenance_monthly,"
    "area_exclusive_m2,floor_current,direction,rooms,"
    "subway_station,subway_distance_m,jibun_address,summary,confirmed_at,agent_phone"
)


def main():
    c = db.connect()
    # ORDER BY로 줄 순서를 고정 → 커밋 사이 diff가 바뀐 매물 몇 줄로 최소화(git 델타 압축).
    rows = c.execute(f"SELECT {COLS} FROM naver_listings ORDER BY article_no").fetchall()
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(dict(r), ensure_ascii=False, default=str) + "\n")
    c.close()
    print(f"  {len(rows)}건 → {OUT}")


if __name__ == "__main__":
    main()
