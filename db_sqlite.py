"""SQLite schema + helpers — 로컬 SQLite 전용 버전 (Supabase 이전 전 원본 보존)."""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "naver_opst.db")


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = connect()
    c = conn.cursor()
    # 동(leaf) 단위 지역 테이블 — 웹 캐스케이딩 드롭다운용
    c.execute("""
    CREATE TABLE IF NOT EXISTS regions (
        cortarNo   TEXT PRIMARY KEY,
        sido       TEXT NOT NULL,   -- 서울시 / 경기도 / 인천시
        sigungu    TEXT NOT NULL,   -- 강남구 / 수원시 장안구 ...
        dong       TEXT NOT NULL,   -- 역삼동
        lat        REAL,
        lon        REAL
    )""")
    # 크롤 진행 상태 (재개용)
    c.execute("""
    CREATE TABLE IF NOT EXISTS crawl_state (
        cortarNo    TEXT PRIMARY KEY,
        status      TEXT,            -- done / partial
        n_articles  INTEGER,
        updated_at  TEXT
    )""")
    # 매물 테이블
    c.execute("""
    CREATE TABLE IF NOT EXISTS listings (
        articleNo     TEXT PRIMARY KEY,
        sido          TEXT,
        sigungu       TEXT,
        dong          TEXT,
        cortarNo      TEXT,
        articleName   TEXT,          -- 단지/건물명
        buildingName  TEXT,
        realEstateType TEXT,         -- 오피스텔
        tradeType     TEXT,          -- 월세
        deposit       INTEGER,       -- 보증금 (만원)
        rent          INTEGER,       -- 월세 (만원)
        area_m2       REAL,          -- 계약면적 area1
        area_real_m2  REAL,          -- 전용면적 area2
        areaName      TEXT,
        floorInfo     TEXT,
        direction     TEXT,
        confirmYmd    TEXT,
        featureDesc   TEXT,
        tags          TEXT,
        lat           REAL,
        lon           REAL,
        realtorName   TEXT,
        cpName        TEXT,
        imgUrl        TEXT,
        articleUrl    TEXT,
        crawled_at    TEXT
    )""")
    for idx in [
        "CREATE INDEX IF NOT EXISTS ix_l_region ON listings(sido,sigungu,dong)",
        "CREATE INDEX IF NOT EXISTS ix_l_deposit ON listings(deposit)",
        "CREATE INDEX IF NOT EXISTS ix_l_rent ON listings(rent)",
        "CREATE INDEX IF NOT EXISTS ix_l_area ON listings(area_real_m2)",
        "CREATE INDEX IF NOT EXISTS ix_r_sido ON regions(sido,sigungu,dong)",
    ]:
        c.execute(idx)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("DB initialized at", DB_PATH)
