# -*- coding: utf-8 -*-
"""
실시간 매물 뷰 nl_live + 보조 인덱스 생성 (idempotent).

부동산 매물 뷰어(web/gangnam_app.py)가 상세 테이블(naver_listings) 대신 이 뷰를 읽어,
목록 크롤(crawler.py)이 listings에 넣는 즉시 사이트에 반영되게 한다(진짜 실시간).

  nl_live = listings(실시간·크롤 대상 지역·최근7일) LEFT JOIN naver_listings(상세 보강)
    - 필터/정렬/집계 컬럼(building_type_code, sido, dong, rent, deposit, area, crawled_at,
      confirmymd)은 listings에서 '직접' 노출 → 인덱스로 빠르게. 타입은 한글명→코드 CASE.
    - 상세 전용 컬럼(rooms, floor_current, subway_*, *_address)만 naver_listings에서 조인.
    - WHERE: 크롤 대상 지역(common/target_regions.py) + 월세. 대상 밖 지역은 시세가 낡아 숨김
      (DB엔 유지). 삭제된 매물은 별도 정리.

  python pipeline/naver/create_live_view.py
"""
import os
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _BASE)
sys.path.insert(0, os.path.join(_BASE, "common"))
import db
import target_regions

# 뷰 노출 지역 = 크롤 대상 지역(수도권 + 부산·천안). 뷰 정의엔 파라미터를 못 쓰므로 리터럴로.
_REGION = target_regions.sql_literal('l.sido', 'l.sigungu')

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
    # 집계 커버링(index-only 스캔): 무필터 count/by_type/dong distinct를 넓은 heap 대신 인덱스로.
    "CREATE INDEX IF NOT EXISTS ix_listings_agg ON listings (sido, crawled_at, realestatetype, dong)",
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
WHERE {_REGION}
  AND l.tradetype = '월세'
  -- (최신일 필터 제거: 부분 크롤에도 뷰 공백 방지. 삭제매물은 별도 정리)
"""

# 집계/카운트 전용: 조인 없는 listings-only(같은 컬럼명, 상세 컬럼은 NULL). index-only로 빠름.
VIEW_BASE = f"""
CREATE VIEW nl_base AS
SELECT
  l.articleno::bigint AS article_no,
  COALESCE(NULLIF(l.buildingname,''), l.articlename) AS building_name,
  {_BTYPE.format(c='l')} AS building_type_code,
  l.sido, l.sigungu, l.dong, l.deposit, l.rent AS rent_monthly,
  l.mgmt AS maintenance_monthly,
  COALESCE(l.area_real_m2, l.area_m2) AS area_exclusive_m2,
  NULL::int AS floor_current, NULL::int AS rooms, l.direction,
  NULL::text AS subway_station, NULL::int AS subway_distance_m,
  l.featuredesc AS summary, l.tags AS summary_tags,
  l.lat, l.lon AS lng,
  NULL::text AS jibun_address, NULL::text AS road_address,
  CASE WHEN l.confirmymd ~ '^[0-9]{{8}}$'
       THEN to_char(to_date(l.confirmymd,'YYYYMMDD'),'YYYY-MM-DD') END AS confirmed_at,
  l.crawled_at,
  NULLIF(l.confirmymd,'') AS confirmed_sort
FROM listings l
WHERE {_REGION}
  AND l.tradetype = '월세'
  -- (최신일 필터 제거: 부분 크롤에도 뷰 공백 방지. 삭제매물은 별도 정리)
"""


def main():
    conn = db.connect()
    for ix in INDEXES:
        conn.execute(ix); conn.commit()
        print("index:", ix.split()[5])
    # 컬럼 순서가 바뀌면 CREATE OR REPLACE 가 거부되므로 DROP 후 재생성.
    conn.execute("DROP VIEW IF EXISTS nl_live"); conn.commit()
    conn.execute(VIEW.replace("CREATE OR REPLACE VIEW", "CREATE VIEW")); conn.commit()
    conn.execute("DROP VIEW IF EXISTS nl_base"); conn.commit()
    conn.execute(VIEW_BASE); conn.commit()
    conn.execute("VACUUM ANALYZE listings"); conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM nl_live").fetchone()[0]
    print(f"nl_live/nl_base 생성 완료 — 현재 {n:,}건")
    conn.close()


if __name__ == "__main__":
    main()
