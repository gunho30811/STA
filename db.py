"""Postgres (Supabase) schema + helpers shared by crawler and web app."""
import os
import re

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

# PK per table — used to convert INSERT OR REPLACE → INSERT ... ON CONFLICT
_PK = {
    'regions': 'cortarNo',
    'crawl_state': 'cortarNo',
    'listings': 'articleNo',
    'naver_listings': 'article_no',
}


def _to_pg(sql):
    """SQLite SQL → Postgres: ? → %s, INSERT OR REPLACE → UPSERT."""
    sql = sql.replace('?', '%s')
    m = re.search(
        r'INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES',
        sql, re.IGNORECASE,
    )
    if m:
        table, cols_raw = m.group(1), m.group(2)
        pk = _PK.get(table)
        if pk:
            cols = [c.strip() for c in cols_raw.split(',')]
            sql = re.sub(r'INSERT\s+OR\s+REPLACE\s+INTO', 'INSERT INTO',
                         sql, flags=re.IGNORECASE)
            upd = ', '.join(f'{c}=EXCLUDED.{c}' for c in cols if c != pk)
            sql += f' ON CONFLICT ({pk}) DO UPDATE SET {upd}'
    return sql


class _Row:
    """Row supporting both r['col'] and r[0] access — same as sqlite3.Row."""
    __slots__ = ('_d', '_v')

    def __init__(self, description, values):
        self._d = {d.name: v for d, v in zip(description, values)}
        self._v = tuple(values)

    def __getitem__(self, key):
        return self._v[key] if isinstance(key, int) else self._d[key]

    def keys(self):
        return self._d.keys()

    def items(self):
        return self._d.items()

    def get(self, key, default=None):
        return self._d.get(key, default)

    def __iter__(self):
        return iter(self._v)

    def __repr__(self):
        return repr(self._d)


class _Cursor:
    def __init__(self, pgcur):
        self._cur = pgcur

    def _wrap(self, row):
        if row is None or self._cur.description is None:
            return row
        return _Row(self._cur.description, row)

    def fetchone(self):
        return self._wrap(self._cur.fetchone())

    def fetchall(self):
        if not self._cur.description:
            return []
        desc = self._cur.description
        return [_Row(desc, r) for r in self._cur.fetchall()]

    def __iter__(self):
        if self._cur.description:
            desc = self._cur.description
            for row in self._cur:
                yield _Row(desc, row)

    @property
    def rowcount(self):
        return self._cur.rowcount


class _Conn:
    """psycopg2 connection with a sqlite3-compatible interface."""

    def __init__(self):
        url = os.environ.get('DATABASE_URL')
        if not url:
            raise RuntimeError('DATABASE_URL 환경변수를 설정하세요 (.env 또는 Railway 환경변수).')
        self._conn = psycopg2.connect(url)

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(_to_pg(sql), params or None)
        return _Cursor(cur)

    def executemany(self, sql, seq):
        cur = self._conn.cursor()
        psycopg2.extras.execute_batch(cur, _to_pg(sql), seq, page_size=500)
        return _Cursor(cur)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def rollback(self):
        self._conn.rollback()


def connect():
    return _Conn()


def init_db():
    conn = connect()
    conn.execute("""
    CREATE TABLE IF NOT EXISTS regions (
        cortarNo   TEXT PRIMARY KEY,
        sido       TEXT NOT NULL,
        sigungu    TEXT NOT NULL,
        dong       TEXT NOT NULL,
        lat        REAL,
        lon        REAL
    )""")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS crawl_state (
        cortarNo    TEXT PRIMARY KEY,
        status      TEXT,
        n_articles  INTEGER,
        updated_at  TEXT
    )""")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS listings (
        articleNo      TEXT PRIMARY KEY,
        sido           TEXT,
        sigungu        TEXT,
        dong           TEXT,
        cortarNo       TEXT,
        articleName    TEXT,
        buildingName   TEXT,
        realEstateType TEXT,
        tradeType      TEXT,
        deposit        INTEGER,
        rent           INTEGER,
        area_m2        REAL,
        area_real_m2   REAL,
        areaName       TEXT,
        floorInfo      TEXT,
        direction      TEXT,
        confirmYmd     TEXT,
        featureDesc    TEXT,
        tags           TEXT,
        lat            REAL,
        lon            REAL,
        realtorName    TEXT,
        cpName         TEXT,
        imgUrl         TEXT,
        articleUrl     TEXT,
        crawled_at     TEXT,
        mgmt           INTEGER
    )""")
    # 상세 수집 결과 (SCHEMA.md naver_listings). listings(목록)를 상세 API 3종 + 좌표
    # 역계산으로 보강한 결과 테이블. JSON 컬럼(summary_tags/facilities/agent_phone/
    # subway_500m/subway_1km)은 JSON 문자열을 담는 TEXT 로 저장.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS naver_listings (
        article_no                    BIGINT PRIMARY KEY,
        url                           TEXT,
        building_type                 TEXT,
        confirmed_at                  TEXT,
        posted_at                     TEXT,
        summary                       TEXT,
        summary_tags                  TEXT,
        deposit                       INTEGER,
        rent_monthly                  INTEGER,
        maintenance_monthly           INTEGER,
        maintenance_type              TEXT,
        area_contract_m2              REAL,
        area_exclusive_m2             REAL,
        exclusive_ratio               INTEGER,
        floor_current                 INTEGER,
        floor_total                   INTEGER,
        rooms                         INTEGER,
        bathrooms                     INTEGER,
        direction                     TEXT,
        entrance_type                 TEXT,
        duplex                        BOOLEAN,
        move_in                       TEXT,
        facilities                    TEXT,
        road_address                  TEXT,
        jibun_address                 TEXT,
        building_name                 TEXT,
        lat                           REAL,
        lng                           REAL,
        building_use                  TEXT,
        approval_date                 TEXT,
        building_age                  INTEGER,
        households                    INTEGER,
        households_same_area          INTEGER,
        heating                       TEXT,
        parking_total                 INTEGER,
        parking_per_household         REAL,
        floor_area_ratio              INTEGER,
        building_coverage_ratio       INTEGER,
        builder                       TEXT,
        dong_count                    INTEGER,
        agent_office                  TEXT,
        agent_name                    TEXT,
        agent_phone                   TEXT,
        agent_address                 TEXT,
        agent_reg_no                  TEXT,
        agent_owner_confirmed_3m      INTEGER,
        broker_fee_max                REAL,
        broker_fee_rate               REAL,
        school_name                   TEXT,
        school_type                   TEXT,
        school_walk_min               INTEGER,
        school_student_per_teacher    REAL,
        subway_station                TEXT,
        subway_distance_m             INTEGER,
        subway_500m                   TEXT,
        subway_1km                    TEXT,
        subway_walk_min               INTEGER,
        same_building_same_area_count INTEGER,
        sido                          TEXT,
        sigungu                       TEXT,
        dong                          TEXT,
        cortarno                      TEXT,
        crawled_at                    TEXT
    )""")
    for idx in [
        "CREATE INDEX IF NOT EXISTS ix_l_region ON listings(sido,sigungu,dong)",
        "CREATE INDEX IF NOT EXISTS ix_l_deposit ON listings(deposit)",
        "CREATE INDEX IF NOT EXISTS ix_l_rent ON listings(rent)",
        "CREATE INDEX IF NOT EXISTS ix_l_area ON listings(area_real_m2)",
        "CREATE INDEX IF NOT EXISTS ix_r_sido ON regions(sido,sigungu,dong)",
        "CREATE INDEX IF NOT EXISTS ix_nl_region ON naver_listings(sido,sigungu,dong)",
        "CREATE INDEX IF NOT EXISTS ix_nl_rent ON naver_listings(rent_monthly)",
        "CREATE INDEX IF NOT EXISTS ix_nl_building ON naver_listings(building_name)",
    ]:
        conn.execute(idx)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Supabase Postgres DB initialized.")
