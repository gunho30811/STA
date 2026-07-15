# -*- coding: utf-8 -*-
"""
실시간 매물 뷰 nl_live + 보조 인덱스 생성 (idempotent).

부동산 매물 뷰어(web/gangnam_app.py)가 상세 테이블(naver_listings) 대신 이 뷰를 읽어,
목록 크롤(crawler.py)이 listings에 넣는 즉시 사이트에 반영되게 한다(진짜 실시간).

  nl_live = listings(실시간·수도권·최근7일) LEFT JOIN naver_listings(상세 보강)
    - 필터/정렬/집계 컬럼(building_type_code, sido, dong, rent, deposit, area, crawled_at,
      confirmymd)은 listings에서 '직접' 노출 → 인덱스로 빠르게. 타입은 한글명→코드 CASE.
    - 상세 전용 컬럼(rooms, floor_current, subway_*, *_address)만 naver_listings에서 조인.
    - WHERE: 수도권(서울/경기/인천) + 최근 7일(crawled_at) = 내려간 매물 자동 숨김(DB엔 유지).

  python pipeline/naver/create_live_view.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import db

# 한글 매물명 → 네이버 코드 (listings.realestatetype 은 realEstateTypeName(한글)을 저장함)
_BTYPE = (
    "CASE {c}.realestatetype "
    "WHEN '아파트' THEN 'APT' WHEN '오피스텔' THEN 'OPST' WHEN '빌라' THEN 'VL' "
    "WHEN '원룸' THEN 'OR' WHEN '단독/다가구' THEN 'DDDGG' WHEN '상가' THEN 'SG' "
    "WHEN '전원주택' THEN 'JWJT' ELSE {c}.realestatetype END"
)

INDEXES = [
    f"CREATE INDEX IF NOT EXISTS ix_listings_btype ON listings (({_BTYPE.format(c='listings')}))",
    "CREATE INDEX IF NOT EXISTS ix_listings_crawled ON listings(crawled_at)",
    "CREATE INDEX IF NOT EXISTS ix_listings_sido_dong ON listings(sido,dong)",
    "CREATE INDEX IF NOT EXISTS ix_listings_dong ON listings(dong)",
    "CREATE INDEX IF NOT EXISTS ix_listings_confirmymd ON listings(confirmymd)",
]

VIEW = f"""
CREATE OR REPLACE VIEW nl_live AS
SELECT
  l.articleno::bigint AS article_no,
  COALESCE(n.building_name, NULLIF(l.buildingname,''), l.articlename) AS building_name,
  {_BTYPE.format(c='l')} AS building_type_code,
  l.sido, l.sigungu, l.dong, l.deposit, l.rent AS rent_monthly,
  COALESCE(n.maintenance_monthly, l.mgmt) AS maintenance_monthly,
  COALESCE(l.area_real_m2, l.area_m2) AS area_exclusive_m2,
  n.floor_current, n.rooms, l.direction,
  n.subway_station, n.subway_distance_m,
  COALESCE(n.summary, l.featuredesc) AS summary,
  COALESCE(n.summary_tags, l.tags) AS summary_tags,
  l.lat, l.lon AS lng,
  n.jibun_address, n.road_address,
  COALESCE(n.confirmed_at, CASE WHEN l.confirmymd ~ '^[0-9]{{8}}$'
     THEN to_char(to_date(l.confirmymd,'YYYYMMDD'),'YYYY-MM-DD') END) AS confirmed_at,
  l.crawled_at,
  NULLIF(l.confirmymd,'') AS confirmed_sort
FROM listings l
LEFT JOIN naver_listings n ON n.article_no = l.articleno::bigint
WHERE l.sido IN ('서울시','경기도','인천시')
  AND l.crawled_at >= to_char(now() - interval '7 days','YYYY-MM-DD')
"""


def main():
    conn = db.connect()
    for ix in INDEXES:
        conn.execute(ix); conn.commit()
        print("index:", ix.split()[5])
    # 컬럼 순서가 바뀌면 CREATE OR REPLACE 가 거부되므로 DROP 후 재생성.
    conn.execute("DROP VIEW IF EXISTS nl_live"); conn.commit()
    conn.execute(VIEW.replace("CREATE OR REPLACE VIEW", "CREATE VIEW")); conn.commit()
    conn.execute("ANALYZE listings"); conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM nl_live").fetchone()[0]
    print(f"nl_live 생성 완료 — 현재 {n:,}건")
    conn.close()


if __name__ == "__main__":
    main()
