# -*- coding: utf-8 -*-
"""로컬 SQLite 백엔드 — db.py(Supabase/Postgres)의 드롭인 대체.

GUI '로컬 폴더' 모드에서 `sys.modules['db'] = local_db` 로 주입하면, 크롤러/스냅샷/
export 가 하던 `import db` 가 이 모듈을 받아 **선택한 폴더의 samsam.db(SQLite)** 에 적재한다.
매 실행마다 upsert 로 같은 파일에 쌓인다(누적).

samsam 크롤러는 Postgres 방언(`%s` 플레이스홀더, `ON CONFLICT ... EXCLUDED`,
`ALTER ... ADD COLUMN IF NOT EXISTS`)을 직접 쓰므로 여기서 SQLite 로 맞춰준다:
  - `%s` → `?`  (executemany/execute 시 치환)
  - `ON CONFLICT ... DO UPDATE SET x=EXCLUDED.x` → SQLite 도 3.24+ 에서 그대로 지원
  - `ALTER ... ADD COLUMN IF NOT EXISTS` → IF NOT EXISTS 제거 후 '중복 컬럼' 에러는 무시

DB 파일 경로: 환경변수 SAMSAM_SQLITE_PATH. 없으면 ./samsam.db.
"""
import os
import re
import sqlite3


def _db_path():
    return os.environ.get('SAMSAM_SQLITE_PATH') or os.path.abspath('samsam.db')


def _to_sqlite(sql):
    """Postgres 방언 → SQLite. 플레이스홀더 %s → ? 치환."""
    return sql.replace('%s', '?')


class _Row:
    """r['col'] 과 r[0] 둘 다 되고 .get()/keys()/dict(r) 도 되는 행 (db._Row 와 동일 계약)."""
    __slots__ = ('_d', '_v')

    def __init__(self, cols, values):
        self._d = {c: v for c, v in zip(cols, values)}
        self._v = tuple(values)

    def __getitem__(self, key):
        return self._v[key] if isinstance(key, int) else self._d[key]

    def get(self, key, default=None):
        return self._d.get(key, default)

    def keys(self):
        return self._d.keys()

    def items(self):
        return self._d.items()

    def __iter__(self):
        return iter(self._v)

    def __repr__(self):
        return repr(self._d)


def _row_factory(cursor, row):
    return _Row([d[0] for d in cursor.description], row)


_ADD_COL_RE = re.compile(
    r'ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+(.+)',
    re.IGNORECASE | re.DOTALL)


class _Conn:
    """db.py 의 _Conn 과 같은 표면(execute/executemany/commit/close 등)을 SQLite 로 구현."""

    def __init__(self):
        self._c = sqlite3.connect(_db_path(), timeout=60)
        self._c.row_factory = _row_factory
        self._c.execute('PRAGMA journal_mode=WAL')

    def execute(self, sql, params=()):
        # ALTER ... ADD COLUMN IF NOT EXISTS → SQLite 에 없는 구문. IF NOT EXISTS 빼고 실행,
        # 이미 있으면(duplicate column) 무시.
        m = _ADD_COL_RE.match(sql.strip())
        if m:
            try:
                return self._c.execute(f'ALTER TABLE {m.group(1)} ADD COLUMN {m.group(2)}')
            except sqlite3.OperationalError as e:
                if 'duplicate column' in str(e).lower():
                    return self._c.execute('SELECT 1')  # no-op
                raise
        sql = _to_sqlite(sql)
        return self._c.execute(sql, params) if params else self._c.execute(sql)

    def executemany(self, sql, seq):
        return self._c.executemany(_to_sqlite(sql), list(seq))

    def commit(self):
        self._c.commit()

    def rollback(self):
        self._c.rollback()

    def close(self):
        self._c.close()

    # 일부 코드가 conn.autocommit 을 만질 수 있어 받아준다(SQLite 에선 무해).
    @property
    def autocommit(self):
        return getattr(self._c, 'autocommit', False)

    @autocommit.setter
    def autocommit(self, v):
        try:
            self._c.autocommit = v
        except Exception:
            pass


def connect():
    return _Conn()


def init_db(force=False):
    """samsam 크롤에 필요한 두 테이블을 로컬 SQLite 에 생성(있으면 유지)."""
    conn = connect()
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
        month_occ             TEXT,
        station_500m_count    INTEGER,
        station_500m_names    TEXT,
        station_1km_count     INTEGER,
        station_1km_names     TEXT,
        sido                  TEXT,
        sigungu               TEXT,
        dong                  TEXT,
        collected_at          TEXT
    )""")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS samsam_snapshots (
        snapshot_date  TEXT,
        sido           TEXT,
        sigungu        TEXT,
        dong           TEXT,
        building_type  TEXT,
        n              INTEGER,
        avg_occ_1m     REAL,
        avg_occ_2m     REAL,
        avg_occ_3m     REAL,
        avg_week       REAL,
        PRIMARY KEY (snapshot_date, sido, sigungu, dong, building_type)
    )""")
    for idx in (
        "CREATE INDEX IF NOT EXISTS ix_sl_region ON samsam_listings(sido,sigungu,dong)",
        "CREATE INDEX IF NOT EXISTS ix_sl_rent ON samsam_listings(rent_total_weekly)",
        "CREATE INDEX IF NOT EXISTS ix_sl_building ON samsam_listings(building_name)",
    ):
        conn.execute(idx)
    conn.commit()
    conn.close()


if __name__ == '__main__':
    init_db()
    print('local SQLite initialized at', _db_path())
