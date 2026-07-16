# -*- coding: utf-8 -*-
"""네이버 로컬 SQLite 백엔드 — db.py(Supabase/Postgres)의 드롭인 대체 (네이버 전용).

네이버 GUI '로컬 폴더' 모드에서 `sys.modules['db'] = naver_local_db` 로 주입하면,
crawler.py / crawl_detail.py / export_jsonl.py 가 하던 `import db` 가 이 모듈을 받아
**선택한 폴더의 naver.db(SQLite)** 에 적재한다. 매 실행마다 upsert 로 같은 파일에 누적.

네이버 크롤러는 원래 SQLite 방언(`?` 플레이스홀더, `INSERT OR REPLACE`)으로 짜여 있어,
공용 SQLite 어댑터(common/local_db.py)의 커넥션을 그대로 재사용한다(연결/행 래핑 동일).
이 모듈은 스키마(regions/crawl_state/listings/naver_listings)만 네이버용으로 만든다.

DB 파일 경로: 환경변수 SAMSAM_SQLITE_PATH(공용 어댑터가 읽음)에 naver.db 경로를 넣어 재사용.
"""
import local_db  # 공용 SQLite 어댑터(_Conn/_Row/%s→? 치환/ALTER IF NOT EXISTS 처리)

# 공용 어댑터의 커넥션을 그대로 사용(경로는 SAMSAM_SQLITE_PATH 환경변수 → GUI가 naver.db로 지정).
connect = local_db.connect

_LISTINGS = """
CREATE TABLE IF NOT EXISTS listings (
    articleNo      TEXT PRIMARY KEY,
    sido           TEXT, sigungu TEXT, dong TEXT, cortarNo TEXT,
    articleName    TEXT, buildingName TEXT, realEstateType TEXT, tradeType TEXT,
    deposit        INTEGER, rent INTEGER,
    area_m2        REAL, area_real_m2 REAL, areaName TEXT,
    floorInfo      TEXT, direction TEXT, confirmYmd TEXT, featureDesc TEXT, tags TEXT,
    lat REAL, lon REAL, realtorName TEXT, cpName TEXT, imgUrl TEXT, articleUrl TEXT,
    crawled_at     TEXT, mgmt INTEGER
)"""

_REGIONS = """
CREATE TABLE IF NOT EXISTS regions (
    cortarNo TEXT PRIMARY KEY, sido TEXT NOT NULL, sigungu TEXT NOT NULL,
    dong TEXT NOT NULL, lat REAL, lon REAL
)"""

_CRAWL_STATE = """
CREATE TABLE IF NOT EXISTS crawl_state (
    cortarNo TEXT PRIMARY KEY, status TEXT, n_articles INTEGER, updated_at TEXT
)"""

# 상세(선택). detail_map/ crawl_detail 이 쓰는 전체 컬럼(SCHEMA.md naver_listings).
_NAVER_LISTINGS = """
CREATE TABLE IF NOT EXISTS naver_listings (
    article_no BIGINT PRIMARY KEY, url TEXT,
    building_type_code TEXT, building_type TEXT,
    confirmed_at TEXT, posted_at TEXT, summary TEXT, summary_tags TEXT, tags TEXT,
    deposit INTEGER, rent_monthly INTEGER, maintenance_monthly INTEGER, maintenance_type TEXT,
    area_contract_m2 REAL, area_exclusive_m2 REAL, exclusive_ratio INTEGER,
    floor_current INTEGER, floor_total INTEGER, rooms INTEGER, bathrooms INTEGER,
    direction TEXT, entrance_type TEXT, duplex BOOLEAN, move_in TEXT, facilities TEXT,
    road_address TEXT, jibun_address TEXT, building_name TEXT, bldg_dong TEXT,
    lat REAL, lng REAL, building_use TEXT, approval_date TEXT, building_age INTEGER,
    households INTEGER, households_same_area INTEGER, heating TEXT,
    parking_total INTEGER, parking_per_household REAL,
    floor_area_ratio INTEGER, building_coverage_ratio INTEGER, builder TEXT, dong_count INTEGER,
    agent_office TEXT, agent_name TEXT, agent_phone TEXT, agent_address TEXT, agent_reg_no TEXT,
    agent_owner_confirmed_3m INTEGER, broker_fee_max REAL, broker_fee_rate REAL,
    school_name TEXT, school_type TEXT, school_walk_min INTEGER, school_student_per_teacher REAL,
    subway_station TEXT, subway_distance_m INTEGER, subway_500m TEXT, subway_1km TEXT,
    subway_walk_min INTEGER, same_building_same_area_count INTEGER,
    sido TEXT, sigungu TEXT, dong TEXT, cortarno TEXT, crawled_at TEXT
)"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_listings_region ON listings(sido,sigungu,dong)",
    "CREATE INDEX IF NOT EXISTS ix_listings_crawled ON listings(crawled_at)",
]


def init_db(force=False):
    """네이버 크롤에 필요한 테이블을 로컬 SQLite 에 생성(있으면 유지)."""
    conn = connect()
    for ddl in (_REGIONS, _CRAWL_STATE, _LISTINGS, _NAVER_LISTINGS):
        conn.execute(ddl)
    for ix in _INDEXES:
        conn.execute(ix)
    conn.commit()
    conn.close()


if __name__ == '__main__':
    init_db()
    print('네이버 로컬 SQLite 초기화 완료:', local_db._db_path())
