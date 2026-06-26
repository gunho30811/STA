"""Postgres (Supabase) schema + helpers shared by crawler and web app."""
import os
import re

from dotenv import load_dotenv

# psycopg2 우선. 빌드 불가 환경(예: Python 3.15 알파, 휠 없음)에선 순수파이썬 pg8000으로 폴백.
try:
    import psycopg2
    import psycopg2.extras
    _DRIVER = 'psycopg2'
except ImportError:
    psycopg2 = None
    try:
        import ssl
        import urllib.parse
        import pg8000.dbapi
        _DRIVER = 'pg8000'
    except ImportError:
        _DRIVER = None

load_dotenv()

# PK per table — used to convert INSERT OR REPLACE → INSERT ... ON CONFLICT
_PK = {
    'regions': 'cortarNo',
    'crawl_state': 'cortarNo',
    'listings': 'articleNo',
    'naver_listings': 'article_no',
    'samsam_listings': 'room_id',
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
        # psycopg2 description은 .name 속성, pg8000은 (name, ...) 튜플.
        self._d = {(d.name if hasattr(d, 'name') else d[0]): v
                   for d, v in zip(description, values)}
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


def _pg8000_connect(url):
    """pg8000으로 Postgres 연결. Supabase 풀러는 자체 CA를 써서 공개 CA로는 검증 불가."""
    u = urllib.parse.urlparse(url)
    ca = os.environ.get('DB_SSL_CA')
    if ca:
        ctx = ssl.create_default_context(cafile=ca)   # CA 파일 지정 시 정식 검증
    else:
        # psycopg2 기본 sslmode와 동일하게 '암호화하되 CA 검증 생략'.
        # 정식 검증을 원하면 환경변수 DB_SSL_CA 에 Supabase CA 인증서 경로를 지정.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return pg8000.dbapi.connect(
        user=urllib.parse.unquote(u.username or ''),
        password=urllib.parse.unquote(u.password or ''),
        host=u.hostname, port=u.port or 5432,
        database=(u.path or '/').lstrip('/'),
        ssl_context=ctx, timeout=30,
    )


class _Conn:
    """Postgres connection with a sqlite3-compatible interface (psycopg2 또는 pg8000)."""

    def __init__(self):
        url = os.environ.get('DATABASE_URL')
        if not url:
            raise RuntimeError('DATABASE_URL 환경변수를 설정하세요 (.env 또는 Railway 환경변수).')
        if _DRIVER == 'psycopg2':
            self._conn = psycopg2.connect(url)
        elif _DRIVER == 'pg8000':
            self._conn = _pg8000_connect(url)
        else:
            raise RuntimeError('Postgres 드라이버가 없습니다 (psycopg2 또는 pg8000 설치 필요).')

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        if params:
            cur.execute(_to_pg(sql), params)
        else:
            cur.execute(_to_pg(sql))
        return _Cursor(cur)

    def executemany(self, sql, seq):
        cur = self._conn.cursor()
        sql = _to_pg(sql)
        if _DRIVER == 'psycopg2':
            psycopg2.extras.execute_batch(cur, sql, seq, page_size=500)
        else:
            cur.executemany(sql, list(seq))
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
        building_type_code            TEXT,
        building_type                 TEXT,
        confirmed_at                  TEXT,
        posted_at                     TEXT,
        summary                       TEXT,
        summary_tags                  TEXT,
        tags                          TEXT,
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
        bldg_dong                     TEXT,
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
    # 기존(이미 생성된) Supabase 테이블엔 CREATE TABLE IF NOT EXISTS 가 새 컬럼을 추가해주지 않으므로 별도 ALTER.
    conn.execute("ALTER TABLE naver_listings ADD COLUMN IF NOT EXISTS building_type_code TEXT")
    conn.execute("ALTER TABLE naver_listings ADD COLUMN IF NOT EXISTS tags TEXT")
    conn.execute("ALTER TABLE naver_listings ADD COLUMN IF NOT EXISTS bldg_dong TEXT")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS samsam_listings (
        room_id               INTEGER PRIMARY KEY,
        url                   TEXT,
        name                  TEXT,
        building_type         TEXT,
        road_address          TEXT,
        jibun_address         TEXT,
        building_name         TEXT,
        floor                 INTEGER,
        lat                   REAL,
        lng                   REAL,
        area_m2               REAL,
        area_pyeong           INTEGER,
        rooms                 INTEGER,
        bathrooms             INTEGER,
        kitchens              INTEGER,
        living_rooms          INTEGER,
        elevator              BOOLEAN,
        parking               BOOLEAN,
        basic_options         TEXT,
        extra_options         TEXT,
        rent_weekly           INTEGER,
        maintenance_weekly    INTEGER,
        rent_total_weekly     INTEGER,
        booked_days_1m        INTEGER,
        booked_days_2m        INTEGER,
        booked_days_3m        INTEGER,
        blocked_days_1m       INTEGER,
        station_500m_count    INTEGER,
        station_500m_names    TEXT,
        station_1km_count     INTEGER,
        station_1km_names     TEXT,
        sido                  TEXT,
        sigungu               TEXT,
        dong                  TEXT,
        collected_at          TEXT
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
        "CREATE INDEX IF NOT EXISTS ix_sl_region ON samsam_listings(sido,sigungu,dong)",
        "CREATE INDEX IF NOT EXISTS ix_sl_rent ON samsam_listings(rent_total_weekly)",
        "CREATE INDEX IF NOT EXISTS ix_sl_building ON samsam_listings(building_name)",
    ]:
        conn.execute(idx)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Supabase Postgres DB initialized.")
