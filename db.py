"""크롤러·웹앱이 공유하는 Postgres 스키마 + 헬퍼.

메인 DB는 오라클 서버의 내부 pg 컨테이너. 다만 네이버 로컬 크롤은 장시간 SSH 터널
쓰기가 유실되는 문제 때문에 지금도 Supabase를 중간 적재소로 경유(적재 후 서버가
sync_from_supabase.sh 로 가져감)하므로, Supabase 풀러(pgbouncer) 대응 코드는 유지한다.

원래 SQLite로 시작한 프로젝트라, 기존 호출부를 고치지 않도록 sqlite3 호환 인터페이스
(? 플레이스홀더, INSERT OR REPLACE, row['col'] 접근)를 Postgres 위에 씌워 제공한다.
"""
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
    'members': 'id',
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
    """pg8000으로 Postgres 연결. SSL은 3가지 경우로 나뉜다:
    - URL에 sslmode=disable: 평문 접속 (오라클 내부 pg·SSH 터널 — SSL 미지원 서버)
    - DB_SSL_CA 환경변수: 지정한 CA 파일로 정식 검증
    - 기본: 암호화하되 CA 검증 생략 (Supabase 풀러가 자체 CA라 공개 CA로는 검증 불가)"""
    u = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(u.query or '')
    if (qs.get('sslmode') or [''])[0] == 'disable':
        ctx = None   # 평문 접속(SSL 미지원 로컬 pg / 터널 경유)
    else:
        ca = os.environ.get('DB_SSL_CA')
        if ca:
            ctx = ssl.create_default_context(cafile=ca)   # CA 파일 지정 시 정식 검증
        else:
            # psycopg2 기본 sslmode와 동일하게 '암호화하되 CA 검증 생략'.
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
            raise RuntimeError('DATABASE_URL 환경변수를 설정하세요 (.env).')
        if _DRIVER == 'psycopg2':
            self._conn = psycopg2.connect(url)
        elif _DRIVER == 'pg8000':
            self._conn = _pg8000_connect(url)
        else:
            raise RuntimeError('Postgres 드라이버가 없습니다 (psycopg2 또는 pg8000 설치 필요).')
        # autocommit: SELECT 후 "idle in transaction"으로 연결이 묶이는 것 방지(풀러 고갈 방지).
        try:
            self._conn.autocommit = True
        except Exception:
            pass

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        if params:
            cur.execute(_to_pg(sql), params)
        else:
            cur.execute(_to_pg(sql))
        return _Cursor(cur)

    def executemany(self, sql, seq):
        sql = _to_pg(sql)
        rows = list(seq)
        if _DRIVER == 'psycopg2':
            cur = self._conn.cursor()
            psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)
            return _Cursor(cur)
        # pg8000 + Supabase 트랜잭션 풀러(pgbouncer): autocommit 상태로 executemany 를 돌리면
        # 풀러가 문장 사이에 서버 연결을 갈아끼워 'unnamed prepared statement does not exist'
        # (SQLSTATE 26000)가 난다. 배치 전체를 명시적 트랜잭션으로 감싸 한 백엔드에 고정한다.
        self._conn.autocommit = False
        try:
            cur = self._conn.cursor()
            cur.executemany(sql, rows)
            self._conn.commit()
        except Exception:
            try:
                self._conn.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                self._conn.autocommit = True
            except Exception:
                pass
        return _Cursor(cur)

    def commit(self):
        # autocommit 연결에선 이미 다 커밋된 상태. 이때 bare 'commit'을 또 보내면
        # 트랜잭션 풀러(pgbouncer)에서 간헐적으로 26000 에러가 나므로 건너뛴다.
        if getattr(self._conn, 'autocommit', False):
            return
        self._conn.commit()

    def close(self):
        self._conn.close()

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass


def connect():
    return _Conn()


def _seed_admin(conn):
    """새 DB 부트스트랩용 관리자 시드 — 환경변수 ADMIN_USERNAME/ADMIN_PASSWORD 가
    둘 다 설정된 경우에만 동작(코드에 계정정보 하드코딩 금지). 이미 있으면 무시."""
    uname = os.environ.get('ADMIN_USERNAME')
    pw = os.environ.get('ADMIN_PASSWORD')
    if not uname or not pw:
        return
    from werkzeug.security import generate_password_hash
    row = conn.execute("SELECT id FROM members WHERE username=%s", (uname,)).fetchone()
    if row:
        return
    conn.execute(
        "INSERT INTO members(username,email,password_hash,name,role,email_verified,approved,created_at) "
        "VALUES(%s,%s,%s,%s,'admin',TRUE,TRUE,%s)",
        (uname, None, generate_password_hash(pw), '관리자',
         __import__('datetime').datetime.now().isoformat(timespec='seconds')))
    conn.commit()
    print(f"[db] 관리자 계정 시드: {uname}")


_INITED = False


# ── 기존 테이블 신규 컬럼 마이그레이션의 '단일 출처' ───────────────────────────────
# ⚠️ 새 컬럼을 추가할 땐 반드시 이 목록에 넣는다. init_db 의 '기존 DB 빠른 경로'가
#    바로 이 목록을 돌려 운영 DB에 반영한다 — 본문 CREATE TABLE 만 고치면 이미 존재하는
#    운영 DB엔 새 컬럼이 절대 들어가지 않는다(2026-08 provider 컬럼 누락 사고의 원인).
# 전부 ADD COLUMN IF NOT EXISTS 라 멱등하고, 신규 DB(전체 경로)에서도 그대로 재실행된다.
_COLUMN_MIGRATIONS = [
    "ALTER TABLE naver_listings ADD COLUMN IF NOT EXISTS building_type_code TEXT",
    "ALTER TABLE naver_listings ADD COLUMN IF NOT EXISTS tags TEXT",
    "ALTER TABLE naver_listings ADD COLUMN IF NOT EXISTS bldg_dong TEXT",
    "ALTER TABLE samsam_listings ADD COLUMN IF NOT EXISTS month_occ TEXT",
    "ALTER TABLE samsam_snapshots ADD COLUMN IF NOT EXISTS avg_occ_2m REAL",
    "ALTER TABLE members ADD COLUMN IF NOT EXISTS approved BOOLEAN DEFAULT FALSE",
    "ALTER TABLE members ADD COLUMN IF NOT EXISTS kakao_id TEXT",
    "ALTER TABLE members ADD COLUMN IF NOT EXISTS kakao_refresh_token_enc TEXT",
    "ALTER TABLE members ADD COLUMN IF NOT EXISTS kakao_notify BOOLEAN DEFAULT FALSE",
    "ALTER TABLE samsam_accounts ADD COLUMN IF NOT EXISTS provider TEXT DEFAULT 'samsam'",
    "ALTER TABLE samsam_chat_rooms ADD COLUMN IF NOT EXISTS host_or_guest TEXT",
    "ALTER TABLE samsam_chat_rooms ADD COLUMN IF NOT EXISTS last_read_at BIGINT",
    "ALTER TABLE samsam_chat_rooms ADD COLUMN IF NOT EXISTS counterpart_nickname TEXT",
    "ALTER TABLE samsam_chat_rooms ADD COLUMN IF NOT EXISTS last_notified_time BIGINT",
]


def _run_column_migrations(conn):
    """신규 컬럼 보강(_COLUMN_MIGRATIONS)을 개별 실행. 한 문장이 실패해도(예: 아직
    없는 테이블) 나머지는 계속 — autocommit 연결이라 문장 간 격리됨."""
    for sql in _COLUMN_MIGRATIONS:
        try:
            conn.execute(sql)
        except Exception as e:
            print(f"[db] 마이그레이션 스킵: {sql[:55]}… ({repr(e)[:60]})", flush=True)
    conn.commit()


def init_db(force=False):
    # 프로세스당 1회만 스키마 점검(서버리스 콜드스타트에서 앱마다 중복 실행 방지).
    global _INITED
    if _INITED and not force:
        return
    conn = connect()
    # 이미 스키마가 있으면 DDL(CREATE/ALTER/INDEX) 전체를 건너뛴다.
    # — 콜드스타트마다 ALTER가 ACCESS EXCLUSIVE 락을 노려 다른 요청과 경합/hang 나는 것 방지.
    if not force:
        try:
            if conn.execute("SELECT to_regclass('public.members')").fetchone()[0]:
                # 기존 DB: 전체 CREATE/INDEX(락 경합·시간 소요)는 건너뛰고, 신규 컬럼
                # 마이그레이션(_COLUMN_MIGRATIONS)만 실행한다. 새 컬럼은 여기로 반드시 반영됨.
                _run_column_migrations(conn)
                _INITED = True
                conn.close()
                return
        except Exception:
            pass
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
    # (기존 테이블 신규 컬럼 보강은 아래 _run_column_migrations 로 일괄 처리 — _COLUMN_MIGRATIONS)
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
    # (samsam_listings.month_occ 등 신규 컬럼은 _COLUMN_MIGRATIONS 로 보강)
    # 예약률 스냅샷(지역×유형 집계). 크롤 회차마다 snapshot.py 가 적재 → 인기 트렌드 추적.
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
    # 삼삼×네이버 통합 수익성 매칭 결과. 예전엔 data/net_profit_integrated.csv 파일로 주고받았으나
    # 크롤/웹 분리(계약=DB)로 이 테이블에 적재한다. 컬럼명은 웹(profit_app) 내부 짧은키와 동일.
    # 크롤 파이프라인(build_integrated → export_net_profit)이 upsert, 웹은 SELECT.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS net_profit (
        id          BIGINT PRIMARY KEY,      -- 삼삼ID(room_id)
        name        TEXT,   btype   TEXT,   rooms   TEXT,
        sido        TEXT,   sigungu TEXT,   dong    TEXT,   station TEXT,
        dongCnt     REAL,   samBldg REAL,   pyeong  REAL,
        wk          REAL,   maxRev  REAL,   realRev REAL,
        bk          REAL,   bl      REAL,
        nRent       REAL,   nDep    REAL,   nEquiv  REAL,   nMgmt   REAL,
        mgmtFlag    TEXT,   nTotal  REAL,   matches REAL,   mult    REAL,
        bldgCnt     REAL,   bldgRentMin REAL, bldgRentMed REAL, bldgRentMax REAL,
        bldg        TEXT,   naverUrl TEXT,  samUrl  TEXT,   monthOcc TEXT,
        phone       TEXT,   office  TEXT,   repRent REAL,   repDep  REAL,   repFloor TEXT
    )""")
    # 미리 계산해둔 무거운 결과 캐시(대시보드 인사이트·추천 후보 등)를 JSON 문자열로 저장.
    # 웹은 요청 때 재계산(20초+) 대신 이 테이블에서 즉시 읽는다 → 콜드스타트에도 빠름.
    # 갱신은 pipeline/refresh_insights.py(크롤 후·크론)가 담당.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS kv_cache (
        k           TEXT PRIMARY KEY,
        data        TEXT,
        updated_at  TEXT
    )""")
    # 회원/로그인. 관리자는 username, 일반회원은 email로 로그인. 비번은 해시 저장.
    # ('users'는 DB 예약어·내장 테이블과 헷갈리기 쉬워 members 로 명명)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS members (
        id              SERIAL PRIMARY KEY,
        username        TEXT UNIQUE,
        email           TEXT UNIQUE,
        password_hash   TEXT NOT NULL,
        name            TEXT,
        birthdate       TEXT,
        role            TEXT DEFAULT 'member',
        email_verified  BOOLEAN DEFAULT FALSE,
        approved        BOOLEAN DEFAULT FALSE,
        kakao_id        TEXT UNIQUE,
        verify_code     TEXT,
        verify_expires  TEXT,
        created_at      TEXT
    )""")
    # (members.approved / kakao_* 등 신규 컬럼은 _COLUMN_MIGRATIONS 로 보강)
    _seed_admin(conn)

    # 삼삼엠투 통합 채팅: 회원이 연결한 삼삼 계정(비번·refreshToken은 암호화해 저장).
    conn.execute("""
    CREATE TABLE IF NOT EXISTS samsam_accounts (
        id                 SERIAL PRIMARY KEY,
        member_id          INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
        provider           TEXT DEFAULT 'samsam',
        samsam_email       TEXT NOT NULL,
        label              TEXT,
        password_enc       TEXT,
        refresh_token_enc  TEXT,
        samsam_member_id   TEXT,
        status             TEXT DEFAULT 'ok',
        last_error         TEXT,
        last_polled_at     TEXT,
        created_at         TEXT
    )""")
    # 계정별 채팅방(삼삼 RTDB live/chatlist/{member_id} 의 방 하나에 대응).
    conn.execute("""
    CREATE TABLE IF NOT EXISTS samsam_chat_rooms (
        id                  SERIAL PRIMARY KEY,
        account_id          INTEGER NOT NULL REFERENCES samsam_accounts(id) ON DELETE CASCADE,
        samsam_room_key     TEXT NOT NULL,
        room_name           TEXT,
        host_or_guest       TEXT,
        counterpart_member  TEXT,
        contract_status     TEXT,
        chat_room_status    TEXT,
        start_date          TEXT,
        end_date            TEXT,
        last_message        TEXT,
        last_message_time   BIGINT,
        updated_at          TEXT,
        UNIQUE (account_id, samsam_room_key)
    )""")
    # (samsam_accounts.provider / samsam_chat_rooms.host_or_guest·last_read_at·
    #  counterpart_nickname·last_notified_time 등 신규 컬럼은 _COLUMN_MIGRATIONS 로 보강)
    # 채팅방별 메시지(RTDB live/messagelist/{room_key}).
    conn.execute("""
    CREATE TABLE IF NOT EXISTS samsam_chat_messages (
        id            SERIAL PRIMARY KEY,
        room_id       INTEGER NOT NULL REFERENCES samsam_chat_rooms(id) ON DELETE CASCADE,
        msg_key       TEXT NOT NULL,
        sender        TEXT,
        receiver      TEXT,
        message       TEXT,
        message_type  TEXT,
        message_time  BIGINT,
        image         TEXT,
        title         TEXT,
        UNIQUE (room_id, msg_key)
    )""")
    # 답장 발송 큐 — 삼삼 쓰기는 REST가 아니라 브라우저 UI 조작(Playwright)으로만 가능해,
    # 웹은 여기 큐잉만 하고 서버의 채팅 폴러(크론 → common/chat_poll.py)가 실제 발송한다.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS samsam_chat_outbox (
        id          SERIAL PRIMARY KEY,
        room_id     INTEGER NOT NULL REFERENCES samsam_chat_rooms(id) ON DELETE CASCADE,
        message     TEXT NOT NULL,
        status      TEXT DEFAULT 'pending',
        last_error  TEXT,
        created_at  TEXT,
        sent_at     TEXT
    )""")
    # 현재 접속자수: 로그인 여부와 무관하게 최근 활동 세션을 핑으로 기록(auth.py before_request).
    conn.execute("""
    CREATE TABLE IF NOT EXISTS visitor_pings (
        session_id  TEXT PRIMARY KEY,
        last_seen   TEXT NOT NULL
    )""")
    # 삼삼 매물 변동(추가/삭제): 크롤 회차마다 수도권 시도별로 신규/사라진 매물 수를 적재.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS samsam_churn (
        crawl_date  TEXT,
        sido        TEXT,
        added       INTEGER,
        removed     INTEGER,
        total       INTEGER,
        PRIMARY KEY (crawl_date, sido)
    )""")
    # 직전 크롤의 라이브 매물 집합(room_id→sido). 다음 크롤과 diff 해 추가/삭제를 계산.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS samsam_live (
        room_id  INTEGER PRIMARY KEY,
        sido     TEXT
    )""")
    # 신규 DB에서도 CREATE에 없는 컬럼(month_occ·kakao_*·provider 등)을 동일 목록으로 보강.
    _run_column_migrations(conn)
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
        "CREATE INDEX IF NOT EXISTS ix_ss_date ON samsam_snapshots(snapshot_date)",
        "CREATE INDEX IF NOT EXISTS ix_ss_region ON samsam_snapshots(sido,sigungu,dong)",
        "CREATE INDEX IF NOT EXISTS ix_members_email ON members(email)",
        "CREATE INDEX IF NOT EXISTS ix_sa_member ON samsam_accounts(member_id)",
        "CREATE INDEX IF NOT EXISTS ix_scr_account ON samsam_chat_rooms(account_id)",
        "CREATE INDEX IF NOT EXISTS ix_scr_last_msg ON samsam_chat_rooms(last_message_time)",
        "CREATE INDEX IF NOT EXISTS ix_scm_room ON samsam_chat_messages(room_id)",
        "CREATE INDEX IF NOT EXISTS ix_scm_time ON samsam_chat_messages(message_time)",
        "CREATE INDEX IF NOT EXISTS ix_sco_status ON samsam_chat_outbox(status)",
        "CREATE INDEX IF NOT EXISTS ix_vp_last_seen ON visitor_pings(last_seen)",
        "CREATE INDEX IF NOT EXISTS ix_churn_date ON samsam_churn(crawl_date)",
        # 지도 bbox 조회용 좌표 인덱스(/samsam/api/map — 없으면 425k 풀스캔으로 팬/줌마다 수 초).
        "CREATE INDEX IF NOT EXISTS ix_listings_latlon ON listings(lat, lon)",
        "CREATE INDEX IF NOT EXISTS ix_nl_latlng ON naver_listings(lat, lng)",
    ]:
        conn.execute(idx)
    conn.commit()
    conn.close()
    _INITED = True


if __name__ == "__main__":
    init_db()
    print("Postgres DB initialized.")
