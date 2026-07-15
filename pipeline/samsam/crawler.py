# -*- coding: utf-8 -*-
"""
삼삼엠투 수도권(서울·경기·인천) 매물 크롤러 → Supabase samsam_listings 적재.

신규 매물은 상세+예약스케줄을 모두 수집하고, 이미 적재된 기존 매물은 예약
스케줄(booked_days_*/blocked_days_1m)만 매일 전부 다시 확인해 예약률을 최신화한다.
매물 수가 많아 예약률 갱신은 동시 요청(REFRESH_WORKERS)으로 처리한다.

신규 매물은 상세+예약스케줄을 모두 수집하고, 이미 적재된 기존 매물은 예약
스케줄(booked_days_*/blocked_days_1m)만 다시 확인해 예약률을 최신화한다.

사용법:
  python pipeline/samsam/crawler.py              # 신규 수집 + 기존 매물 예약률 갱신
  python pipeline/samsam/crawler.py --limit 50   # 신규 N건만 (테스트)
  python pipeline/samsam/crawler.py --redo       # 기존 수집분 전체 재수집(상세 포함)

필요 환경변수 (.env):
  DATABASE_URL, SAMSAM_EMAIL, SAMSAM_PASSWORD
"""
import argparse, getpass, json, os, re, subprocess, sys, threading, time, uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, 'pipeline', 'naver'))
sys.path.insert(0, os.path.join(BASE_DIR, 'pipeline', 'samsam'))   # deploy_lab의 export_jsonl import용
sys.path.insert(0, os.path.join(BASE_DIR, 'common'))   # subway 등 공용 유틸(sta-common 예정)

import db
from subway import stations_within

load_dotenv(os.path.join(BASE_DIR, '.env'))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ── 상수 ──────────────────────────────────────────────────────────────────────
BASE = 'https://web.33m2.co.kr'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
# 브라우저 앱이 API마다 함께 보내는 익명 식별자(abid). 실행당 1개 발급해 세션 내내 고정.
ABID = str(uuid.uuid4())
# 삼삼엠투 API가 실제로 받는 propertyTypes 코드 6종(2026-07 실측).
# 주의: 목록 API가 응답에 싣는 room['propertyType'] 값은 아래 한글 라벨(BTYPE_KO)이며,
#       필터 쿼리(propertyTypes=)에는 반드시 이 영문 코드를 써야 한다. 둘이 다르다.
#       (과거 HOUSE/STORE/OFFICE는 죽은 코드라 0건만 반환 → 단독/원룸/상가주택을 통째로 누락했었음.)
PROPERTY_TYPES = ['OFFICETEL', 'APARTMENT', 'VILLA', 'DETACHED', 'STUDIO', 'MIXED_USE']
BTYPE_KO = {
    'OFFICETEL': '오피스텔', 'APARTMENT': '아파트', 'VILLA': '연립빌라',
    'DETACHED': '단독주택', 'STUDIO': '원룸건물', 'MIXED_USE': '상가주택',
}
BATCH = 50
REQ_SLEEP = 0.5
BLOCK_WAIT = 120
METRO_SIDO = {'서울특별시', '경기도', '인천광역시'}  # 수도권만 수집·갱신 (그 외 지역은 DB에 남아있어도 갱신 안 함)
# 기존 매물 예약률 갱신 동시 요청 수. 서버 차단이 의심되면 env(SAMSAM_REFRESH_WORKERS)로 낮춰 재실험.
REFRESH_WORKERS = int(os.environ.get('SAMSAM_REFRESH_WORKERS', '2'))
REFRESH_CHUNK = 2000      # 이 건수마다 세션(로그인)을 새로 고침

# ── 레이트리밋 대응 ─────────────────────────────────────────────────────────────
# 33m2 스케줄 API는 IP당 짧은 창에서 ~100요청을 넘기면 429로 막고 한동안 안 풀린다.
# 그래서 (1) 하루에 stalest N건만 갱신하는 '로테이션', (2) 429 시 대기·재시도 '백오프',
# (3) 여러 러너 IP로 '샤딩'해 수평 분할한다.
#   - SAMSAM_REFRESH_DAILY_LIMIT: 이번 실행에서 갱신할 최대 매물 수(가장 오래된 것부터). 0=제한없음.
#   - SAMSAM_SHARD='i/N': room_id % N == i 인 매물만 담당(러너별로 다른 IP). 미설정=전체.
#   - SAMSAM_RL_COOLDOWN: 429가 연속으로 누적될 때 쉬는 시간(초). 회복시간에 맞춰 조정.
REFRESH_DAILY_LIMIT = int(os.environ.get('SAMSAM_REFRESH_DAILY_LIMIT', '0'))
# 예약이 이미 잡힌 매물(booked_days_1m>0)은 한동안 안 바뀌므로, 마지막 확인 후 이 기간(일)
# 안이면 재갱신 대상에서 뺀다. 공실(예약 0) 매물은 매 실행 갱신해 '새 예약이 잡히는' 신호를
# 빨리 잡는다. 예약 있던 매물도 쿨다운이 지나면 다시 로테이션에 들어와 취소/변동을 반영한다.
# 0=쿨다운 없음(예약 유무 무관 전부 로테이션).
BOOKED_COOLDOWN_DAYS = int(os.environ.get('SAMSAM_BOOKED_COOLDOWN_DAYS', '7'))
# 실측: ~100요청 소진 시 429, 때리기 멈추면 ~15초 내 회복. 안전하게 20초 쿨다운.
RL_COOLDOWN = int(os.environ.get('SAMSAM_RL_COOLDOWN', '20'))    # 429 시 전 워커 공통 대기(초)
RL_RETRY = int(os.environ.get('SAMSAM_RL_RETRY', '6'))           # 429 요청당 재시도 횟수
EARLY_CHECK = 200         # 예약률 갱신 초반 이 건수까지 데이터 0이면 차단으로 보고 조기 중단

# ── 점진 배포 ───────────────────────────────────────────────────────────────────
# 리프레시 도중 이 건수만큼 DB에 반영될 때마다 lab/*.jsonl을 재생성해 main에 직접 커밋·push한다.
# → 실행이 끝나길 기다리지 않고 배포(Vercel)가 점진적으로 갱신되고, 도중 크래시에도 진행분이 배포됨.
# export가 ORDER BY로 정렬돼 있어 커밋 사이 diff가 작아 repo가 거의 안 큰다. PR 없이 직접 커밋.
# CI(SAMSAM_DEPLOY_PUSH=1)에서만 git push하고, 로컬 실행은 건너뛴다.
DEPLOY_CHUNK = int(os.environ.get('SAMSAM_DEPLOY_CHUNK', '1000'))
DEPLOY_PUSH = os.environ.get('SAMSAM_DEPLOY_PUSH') == '1'


def _parse_shard():
    """SAMSAM_SHARD='i/N' → (i, N). 미설정/형식오류면 (0, 1)=전체."""
    raw = os.environ.get('SAMSAM_SHARD', '').strip()
    if '/' in raw:
        try:
            i, n = raw.split('/', 1)
            i, n = int(i), int(n)
            if n >= 1 and 0 <= i < n:
                return i, n
        except ValueError:
            pass
    return 0, 1

TODAY = date.today()
D30 = TODAY + timedelta(days=30)
D60 = TODAY + timedelta(days=60)
D90 = TODAY + timedelta(days=90)


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


# ── 주소 파싱 ──────────────────────────────────────────────────────────────────
def _strip_floor(addr):
    return re.sub(r'\s*\d+층$', '', addr or '').strip()


def _parse_floor(addr):
    m = re.search(r'(\d+)층', addr or '')
    return int(m.group(1)) if m else None


def _parse_building_name(jibun):
    """지번 주소에서 건물명 추출: '경기도 고양시 일산서구 주엽동 115 대우시티프라자 5층' → '대우시티프라자'"""
    addr = _strip_floor(jibun)
    parts = addr.split()
    for i, p in enumerate(parts):
        # 지번 번호 패턴(115, 115-3) 이후가 건물명
        if re.match(r'^\d+(-\d+)?$', p) and i + 1 < len(parts):
            return ' '.join(parts[i + 1:])
    return ''


def _parse_dong(jibun):
    """지번 주소에서 동(읍/면/리) 추출"""
    for p in _strip_floor(jibun).split():
        if p.endswith(('동', '읍', '면', '리')):
            return p
    return ''


def _parse_sido(addr):
    parts = _strip_floor(addr).split()
    return parts[0] if parts else ''


def _room_sido(room):
    """목록(room) 객체에서 시도 판정 — state 필드 우선, 없으면 주소 파싱."""
    return room.get('state') or _parse_sido(room.get('addrLot') or room.get('addrStreet') or '')


def _parse_sigungu(jibun):
    """시/군/구 단위 — 고양시처럼 구가 따로 있으면 '고양시 일산서구' 형태로."""
    parts = _strip_floor(jibun).split()
    result = []
    for p in parts[1:]:
        if p.endswith(('시', '군')):
            result = [p]
        elif p.endswith('구') and result:
            result.append(p)
            break
        elif result:
            break
    return ' '.join(result)


# ── 스케줄 집계 ────────────────────────────────────────────────────────────────
BOOKED_STATUSES = {'booking'}
BLOCKED_STATUSES = {'disable', 'disabled', 'blocked'}


def _count_status(schedules, from_d, to_d, statuses):
    cnt = 0
    for dt_str, st in (schedules or {}).items():
        try:
            dt = date.fromisoformat(dt_str)
        except ValueError:
            continue
        if from_d <= dt <= to_d and st in statuses:
            cnt += 1
    return cnt


def month_occ(schedules):
    """달력월별 예약/막힘/가용일 집계 → {'YYYY-MM': {'bk':int, 'bl':int, 'days':int}}.

    롤링(오늘~+30/60/90)과 달리 '2026-08처럼 특정 달' 예약률을 보기 위함. days=그 달의 오늘 이후
    일수(현재월은 남은 일수, 미래월은 그 달 전체). 예약률=bk/max(days-bl,1). 과거는 캘린더에 없어
    앞으로 크롤분부터만 채워진다. 오늘~+90일을 덮는 월(fetch_schedules가 조회한 월)만 담는다."""
    import calendar as _cal
    months = {(TODAY.year, TODAY.month)}
    for off in (30, 60, 90):
        dd = TODAY + timedelta(days=off)
        months.add((dd.year, dd.month))
    out = {}
    for (y, m) in sorted(months):
        start_day = TODAY.day if (y, m) == (TODAY.year, TODAY.month) else 1
        out[f"{y:04d}-{m:02d}"] = {"bk": 0, "bl": 0, "days": _cal.monthrange(y, m)[1] - start_day + 1}
    for dt_str, st in (schedules or {}).items():
        try:
            dt = date.fromisoformat(dt_str)
        except ValueError:
            continue
        key = out.get(f"{dt.year:04d}-{dt.month:02d}")
        if key is None or dt < TODAY:
            continue
        if st in BOOKED_STATUSES:
            key["bk"] += 1
        elif st in BLOCKED_STATUSES:
            key["bl"] += 1
    return out


# ── 인증 ───────────────────────────────────────────────────────────────────────
def _get_credentials():
    email = os.environ.get('SAMSAM_EMAIL') or input('삼삼엠투 이메일: ').strip()
    pw = os.environ.get('SAMSAM_PASSWORD') or getpass.getpass('삼삼엠투 비밀번호: ')
    return email, pw


def _mask(email):
    """로그용 이메일 마스킹: 'abcd@x.com' → 'ab***@x.com'."""
    e = email or ''
    if '@' not in e:
        return (e[:2] + '***') if e else '(빈)'
    name, dom = e.split('@', 1)
    return name[:2] + '***@' + dom


def _get_accounts():
    """스케줄 갱신에 쓸 계정 목록. SAMSAM_EMAIL/PASSWORD + (EMAIL2/PASSWORD2 … EMAIL9)를 모은다.

    스케줄 조회는 계정당 일일 소프트한도가 있어(초과 시 200+빈배열), 한 계정이 소진되면 다음
    계정으로 순환해 하루 커버리지를 배로 늘린다. 아무 것도 없으면 대화형 입력으로 폴백(로컬)."""
    accts = []
    e0 = (os.environ.get('SAMSAM_EMAIL') or '').strip()
    p0 = os.environ.get('SAMSAM_PASSWORD') or ''
    if e0 and p0:
        accts.append((e0, p0))
    for i in range(2, 10):
        e = (os.environ.get(f'SAMSAM_EMAIL{i}') or '').strip()
        p = os.environ.get(f'SAMSAM_PASSWORD{i}') or ''
        if e and p:
            accts.append((e, p))
    if not accts:
        accts.append(_get_credentials())
    return accts


def _get_cookies(email, password):
    for attempt in range(8):
        try:
            with sync_playwright() as p:
                b = p.chromium.launch(
                    headless=True,
                    args=['--disable-blink-features=AutomationControlled'],
                )
                ctx = b.new_context(user_agent=UA, locale='ko-KR')
                pg = ctx.new_page()
                pg.goto(f'{BASE}/sign-in', wait_until='networkidle', timeout=40000)
                pg.wait_for_selector('input[type="email"], input[name="email"]', timeout=15000)
                pg.wait_for_timeout(500)

                # 필드 클릭 후 한 글자씩 입력 (자동완성/JS 이벤트 트리거 확실히)
                email_input = pg.locator('input[type="email"], input[name="email"]').first
                email_input.click()
                email_input.fill('')
                email_input.type(email, delay=50)

                pw_input = pg.locator('input[type="password"]').first
                pw_input.click()
                pw_input.fill('')
                pw_input.type(password, delay=50)

                pg.wait_for_timeout(300)

                # 버튼 클릭 후 URL 변경 or networkidle 대기
                btn = pg.locator('button[type="submit"], button:has-text("로그인")').first
                btn.click()
                try:
                    pg.wait_for_url(lambda url: '/sign-in' not in url, timeout=10000)
                except Exception:
                    pg.wait_for_load_state('networkidle', timeout=8000)

                ok = '/sign-in' not in pg.url
                cks = ctx.cookies()
                b.close()
            if ok:
                log("로그인 성공")
                return cks
            log(f"로그인 실패(잔류), 재시도 {attempt + 1}")
        except Exception as e:
            log(f"로그인 예외({repr(e)[:60]}), 재시도 {attempt + 1}")
            time.sleep(3)
    raise RuntimeError("로그인 8회 실패")


def _make_session(cookies):
    s = requests.Session()
    s.headers.update({
        'User-Agent': UA,
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'ko-KR,ko;q=0.9',
        'Origin': BASE,
        'Referer': f'{BASE}/guest/room',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Dest': 'empty',
        # 브라우저(SPA)가 API 호출에 함께 싣는 헤더. 스케줄 API가 SCSS_001은 주면서 데이터를
        # 비우는 소프트차단이 봇 식별 때문일 수 있어, 실제 앱 요청처럼 보이게 맞춘다(쿠키 인증엔 영향 없음).
        'abid': ABID,               # 브라우저 익명 식별자(앱이 항상 보냄)
        'client-language': 'ko',
    })
    for c in cookies:
        s.cookies.set(c['name'], c['value'], domain=c['domain'].lstrip('.'))
    return s


# ── 레이트리밋 게이트 ────────────────────────────────────────────────────────────
# 429는 IP 전역(rolling window)이라 워커마다 따로 재시도하면 계속 때려서 회복이 안 된다.
# 그래서 공유 '재개 시각(resume_at)'을 둬, 한 워커가 429를 만나면 모든 워커가 그 시각까지
# 함께 멈췄다가(때리기 중단→창 회복) 재개한다.
_rl_lock = threading.Lock()
_rl_resume_at = 0.0


def _rl_wait():
    """재개 시각까지 대기(전 워커 공통). 쿨다운 중이면 그만큼 잔다."""
    while True:
        with _rl_lock:
            wait = _rl_resume_at - time.time()
        if wait <= 0:
            return
        time.sleep(min(wait, 3))


def _rl_trip(cooldown):
    """429를 만난 워커가 호출 — 모든 워커의 재개 시각을 now+cooldown 이후로 민다."""
    global _rl_resume_at
    with _rl_lock:
        _rl_resume_at = max(_rl_resume_at, time.time() + cooldown)


# ── API 호출 ───────────────────────────────────────────────────────────────────
def _get(session, url, params=None, stats=None):
    """GET 요청 — 403은 BLOCK_WAIT, 429는 공유 쿨다운 후 재시도. 최종 실패 시 None.

    stats(dict)를 주면 응답 결과를 분류해 카운트한다(진단용):
      'ok'(200+SCSS_001) / 'http_<code>' / 'code_<code>' / 'nojson' / 'exc' / '429_retry'.
    이렇게 남겨야 "성공했지만 예약 0"과 "차단/에러로 빈값"을 사후에 구분할 수 있다.
    """
    def _rec(k):
        if stats is not None:
            with _rl_lock:
                stats[k] = stats.get(k, 0) + 1

    for attempt in range(RL_RETRY + 1):
        _rl_wait()   # 쿨다운 중이면 대기 후 요청
        try:
            r = session.get(url, params=params, timeout=15)
        except Exception:
            _rec('exc')
            return None
        if r.status_code == 403:
            _rec('403_retry')
            time.sleep(BLOCK_WAIT)
            continue
        if r.status_code == 429:
            # 레이트리밋: 전 워커 공통 쿨다운 후 재시도(마지막 시도면 포기).
            _rec('http_429')
            if attempt < RL_RETRY:
                _rl_trip(RL_COOLDOWN)
                _rl_wait()
                continue
            return None
        if r.status_code != 200:
            _rec(f'http_{r.status_code}')
            return None
        try:
            d = r.json()
        except Exception:
            _rec('nojson')
            return None
        if d.get('code') == 'SCSS_001':
            _rec('ok')
            return d
        _rec(f'code_{d.get("code")}')
        return None
    return None


def collect_rids(session):
    """전체 매물 목록 수집. {rid: room(목록객체)} 반환.

    목록 API가 지역(state/province/town)·좌표·주소·가격·평수·방수까지 주므로 room 전체를 보관해
    상세 호출 없이 지역 사전필터 + 다수 컬럼 매핑에 재사용한다.
    """
    rids = {}
    for pt in PROPERTY_TYPES:
        page, cnt, empty = 1, 0, 0
        while page <= 500:
            url = f'{BASE}/v1/use-auth/rooms?propertyTypes={pt}&size=100&sortBy=POPULAR&page={page}'
            d = _get(session, url)
            time.sleep(REQ_SLEEP)
            if d is None:
                break
            content = d.get('data', {}).get('rooms', {}).get('content', [])
            if not content:
                empty += 1
                if empty >= 2:
                    break
                page += 1
                continue
            empty = 0
            for room in content:
                rids[room['rid']] = room
            cnt += len(content)
            if page % 20 == 0:
                log(f"  {pt}: {cnt}개...")
            page += 1
        log(f"{pt}: {cnt}개")
    return rids


def fetch_detail(session, rid):
    """매물 상세. 엔드포인트: GET /v1/use-auth/rooms/{rid}"""
    d = _get(session, f'{BASE}/v1/use-auth/rooms/{rid}')
    time.sleep(REQ_SLEEP)
    return d.get('data') if d else None


def fetch_schedules(session, rid, stats=None):
    """예약 스케줄 → (스케줄 dict, ok). 상태: 'booking'(예약)/'disable'(막힘).

    엔드포인트는 year+month(정수) 필수, 응답은 data.schedules = [{date,status}, ...].
    오늘~+90일을 덮는 월(보통 3~4개)을 각각 호출해 병합.

    ok=True  : 조회한 모든 월이 정상 응답(빈 dict여도 '예약 0'이 확정된 값 → 공실).
    ok=False : 하나라도 요청 실패(차단/에러) → 값 신뢰 불가, DB 갱신에서 제외해야 함.

    스케줄 API는 상태(예약/차단)가 있는 날짜만 돌려주므로, 완전 공실 매물은
    정상적으로 빈 배열을 반환한다. 이 정상 빈값을 실패와 뭉뚱그리면 공실의 예약률(0%)을
    영영 기록하지 못하므로 ok 플래그로 반드시 구분한다.
    """
    out = {}
    months = {(TODAY.year, TODAY.month)}
    for off in (30, 60, 90):
        dd = TODAY + timedelta(days=off)
        months.add((dd.year, dd.month))
    ok = True
    for (y, m) in sorted(months):
        d = _get(session, f'{BASE}/v1/use-auth/rooms/{rid}/schedules',
                 params={'year': y, 'month': m}, stats=stats)
        time.sleep(REQ_SLEEP)
        if d is None:
            ok = False   # 한 달이라도 실패하면 예약수 undercount 위험 → 전체를 신뢰 불가로.
            continue
        for e in (d.get('data', {}).get('schedules') or []):
            if e.get('date'):
                out[e['date']] = e.get('status')
    return out, ok


# ── 행 매핑 ────────────────────────────────────────────────────────────────────
_PARK_NO = {None, '', 'IMPOSSIBLE', 'UNAVAILABLE', 'NONE', 'NO'}


def map_row(rid, room, detail, schedules):
    """목록(room) + 상세(detail) + 스케줄 → samsam_listings 컬럼 dict.

    주소·지역·좌표·평수·가격·방수는 목록 API(room)가 직접 주고, 면적·옵션·엘베·주차는 상세(detail).
    옵션값은 영문 코드(예: TV, REFRIGERATOR) 그대로 저장 — 표시는 뷰어에서 한글 매핑.
    """
    room = room or {}
    detail = detail or {}

    road_raw = room.get('addrStreet') or detail.get('addrStreet') or ''
    jibun_raw = room.get('addrLot') or detail.get('addrLot') or ''
    floor = _parse_floor(jibun_raw) or _parse_floor(road_raw)
    bldg_name = _parse_building_name(jibun_raw)

    lat = room.get('lat') or detail.get('lat')
    lng = room.get('lng') or detail.get('lng')

    area_py = room.get('pyeongSize') or detail.get('pyeongSize')
    area_m2 = detail.get('squareMeterSize')
    if not area_m2 and area_py:
        area_m2 = round(area_py * 3.305785, 1)

    rent_w = room.get('usingFee') or detail.get('usingFee') or 0
    mgmt_w = room.get('mgmtFee') or detail.get('mgmtFee') or 0
    total_w = rent_w + mgmt_w

    # 지역은 목록 API가 직접 제공(state=시도, province=시군구, town=동). 없으면 주소 파싱 폴백.
    sido = room.get('state') or _parse_sido(jibun_raw or road_raw)
    sigungu = room.get('province') or _parse_sigungu(jibun_raw or road_raw)
    dong = room.get('town') or _parse_dong(jibun_raw or road_raw)

    bk1 = _count_status(schedules, TODAY, D30, BOOKED_STATUSES)
    bk2 = _count_status(schedules, TODAY, D60, BOOKED_STATUSES)
    bk3 = _count_status(schedules, TODAY, D90, BOOKED_STATUSES)
    bl1 = _count_status(schedules, TODAY, D30, BLOCKED_STATUSES)

    sub500 = sub1k = []
    if lat and lng:
        sub500 = stations_within(lat, lng, 500)
        sub1k = stations_within(lat, lng, 1000)

    basic = detail.get('basicOptions') or []
    extra = detail.get('additionalOptions') or []

    return {
        'room_id': rid,
        'url': f'{BASE}/guest/room/{rid}',
        'name': room.get('roomName') or detail.get('roomName') or '',
        'building_type': room.get('propertyType') or detail.get('propertyType') or '',
        'road_address': _strip_floor(road_raw),
        'jibun_address': _strip_floor(jibun_raw),
        'building_name': bldg_name,
        'floor': floor,
        'lat': lat,
        'lng': lng,
        'area_m2': area_m2,
        'area_pyeong': area_py,
        'rooms': room.get('roomCnt') or detail.get('roomCnt') or 1,
        'bathrooms': room.get('bathroomCnt') or detail.get('bathroomCnt') or 0,
        'kitchens': room.get('cookroomCnt') or detail.get('cookroomCnt') or 0,
        'living_rooms': room.get('sittingroomCnt') or detail.get('sittingroomCnt') or 0,
        'elevator': bool(detail.get('hasElevator')),
        'parking': detail.get('parkingType') not in _PARK_NO,
        'basic_options': json.dumps(basic, ensure_ascii=False),
        'extra_options': json.dumps(extra, ensure_ascii=False),
        'rent_weekly': rent_w,
        'maintenance_weekly': mgmt_w,
        'rent_total_weekly': total_w,
        'booked_days_1m': bk1,
        'booked_days_2m': bk2,
        'booked_days_3m': bk3,
        'blocked_days_1m': bl1,
        'month_occ': json.dumps(month_occ(schedules), ensure_ascii=False),
        'station_500m_count': len(sub500),
        'station_500m_names': json.dumps(sub500, ensure_ascii=False),
        'station_1km_count': len(sub1k),
        'station_1km_names': json.dumps(sub1k, ensure_ascii=False),
        'sido': sido,
        'sigungu': sigungu,
        'dong': dong,
        'collected_at': datetime.now().isoformat(timespec='seconds'),
    }


# ── 적재 ───────────────────────────────────────────────────────────────────────
COLS = [
    'room_id', 'url', 'name', 'building_type', 'road_address', 'jibun_address',
    'building_name', 'floor', 'lat', 'lng', 'area_m2', 'area_pyeong',
    'rooms', 'bathrooms', 'kitchens', 'living_rooms', 'elevator', 'parking',
    'basic_options', 'extra_options', 'rent_weekly', 'maintenance_weekly',
    'rent_total_weekly', 'booked_days_1m', 'booked_days_2m', 'booked_days_3m',
    'blocked_days_1m', 'month_occ', 'station_500m_count', 'station_500m_names',
    'station_1km_count', 'station_1km_names', 'sido', 'sigungu', 'dong', 'collected_at',
]


def upsert_batch(conn, rows):
    ph = ', '.join(['%s'] * len(COLS))
    cols_sql = ', '.join(COLS)
    upd = ', '.join(f'{c}=EXCLUDED.{c}' for c in COLS if c != 'room_id')
    sql = (f'INSERT INTO samsam_listings ({cols_sql}) VALUES ({ph}) '
           f'ON CONFLICT (room_id) DO UPDATE SET {upd}')
    conn.executemany(sql, [[r.get(c) for c in COLS] for r in rows])
    conn.commit()


def update_schedules_batch(conn, rows):
    """기존 매물의 예약 스케줄만 갱신 (rows: [(bk1, bk2, bk3, bl1, month_occ, collected_at, room_id), ...])."""
    sql = ('UPDATE samsam_listings SET booked_days_1m=%s, booked_days_2m=%s, booked_days_3m=%s, '
           'blocked_days_1m=%s, month_occ=%s, collected_at=%s WHERE room_id=%s')
    conn.executemany(sql, rows)
    conn.commit()


# ── 점진 배포 ───────────────────────────────────────────────────────────────────
def _git(*args):
    """BASE_DIR에서 git 명령 실행 → CompletedProcess."""
    return subprocess.run(['git', *args], cwd=BASE_DIR,
                          capture_output=True, text=True, encoding='utf-8', errors='replace')


def deploy_lab(reason):
    """현재 DB를 lab/*.jsonl로 export하고 main에 직접 커밋·push한다(PR 없음).

    CI(SAMSAM_DEPLOY_PUSH=1)에서만 push한다. 로컬 실행에선 파일만 갱신하지 않고 통째로 스킵.
    main이 그새 움직였을 수 있으니 커밋 후 rebase pull → push. 실패해도 크롤은 계속(다음 청크에서 재시도).
    """
    if not DEPLOY_PUSH:
        return
    try:
        import export_jsonl
        export_jsonl.main()   # DB → lab/*.jsonl 재생성(ORDER BY 정렬)
        _git('add', 'lab/samsam_listings.jsonl', 'lab/samsam_snapshots.jsonl')
        if _git('diff', '--cached', '--quiet').returncode == 0:
            return   # 변경 없음(커밋할 것 없음)
        _git('commit', '-m', f'chore(samsam): 예약률 갱신 배포 — {reason}')
        _git('pull', '--rebase', 'origin', 'main')   # 그새 올라온 커밋 반영
        p = _git('push', 'origin', 'HEAD:main')
        if p.returncode == 0:
            log(f"배포 커밋·push 완료 — {reason}")
        else:
            log(f"배포 push 실패({reason}) rc={p.returncode}: {(p.stderr or '')[:150]}")
    except Exception as e:
        log(f"배포 예외({reason}): {repr(e)[:150]}")


# ── 매물 변동(추가/삭제) 집계 ─────────────────────────────────────────────────
def record_churn(conn, rids):
    """이번 크롤 라이브 매물을 직전(samsam_live)과 비교해 시도별 추가/삭제/총계를 samsam_churn 에
    적재하고, samsam_live 를 이번 집합으로 교체한다. 첫 실행(직전셋 없음)은 기준선만 저장한다.
    rids: {room_id: room(목록객체)} — 이미 수도권으로 필터된 라이브 집합."""
    cur = {}
    for rid, room in rids.items():
        try:
            cur[int(rid)] = _room_sido(room)
        except (TypeError, ValueError):
            continue
    prev = {r[0]: r[1] for r in conn.execute("SELECT room_id, sido FROM samsam_live").fetchall()}
    if prev:
        today = date.today().isoformat()
        added, removed, total = defaultdict(int), defaultdict(int), defaultdict(int)
        for rid, sido in cur.items():
            total[sido] += 1
            if rid not in prev:
                added[sido] += 1
        for rid, sido in prev.items():
            if rid not in cur:
                removed[sido] += 1
        for sido in set(total) | set(removed):
            conn.execute(
                "INSERT INTO samsam_churn(crawl_date,sido,added,removed,total) VALUES(%s,%s,%s,%s,%s) "
                "ON CONFLICT (crawl_date,sido) DO UPDATE SET added=EXCLUDED.added,"
                "removed=EXCLUDED.removed,total=EXCLUDED.total",
                (today, sido, added[sido], removed[sido], total[sido]))
        short = lambda s: s.replace('특별시', '').replace('광역시', '').replace('도', '')
        log("매물 변동 집계(" + today + "): "
            + ", ".join(f"{short(s)} +{added[s]}/-{removed[s]}" for s in sorted(total)))
    else:
        log("매물 변동: 첫 실행 — 기준선만 저장(집계는 다음 회차부터)")
    conn.execute("DELETE FROM samsam_live")
    if cur:
        conn.executemany("INSERT INTO samsam_live(room_id,sido) VALUES(%s,%s)",
                         [[rid, sido] for rid, sido in cur.items()])
    conn.commit()


# ── 메인 ───────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--redo', action='store_true', help='기존 수집분 재수집')
    ap.add_argument('--sigungu', default='', help='시군구 필터 예) 강남구')
    args = ap.parse_args()

    accounts = _get_accounts()
    acct_i = 0
    email, pw = accounts[acct_i]
    log(f"계정 {len(accounts)}개 로드 — 1번({_mask(email)})으로 시작")
    cookies = _get_cookies(email, pw)
    session = _make_session(cookies)

    conn = db.connect()
    # 달력월별 예약(month_occ) 컬럼 자동 마이그레이션(idempotent) — 크롤 전 CI/로컬에서 1회 보장.
    # init_db는 기존 스키마면 DDL을 스킵하므로 여기서 직접 ADD COLUMN IF NOT EXISTS.
    conn.execute("ALTER TABLE samsam_listings ADD COLUMN IF NOT EXISTS month_occ TEXT")
    conn.commit()

    # 이미 적재된 room_id + 마지막 갱신 시각(로테이션에 사용) + 현재 예약 상태(공실 우선용)
    done = set()
    coll = {}    # room_id → collected_at (오래된 것부터 갱신하기 위한 정렬 키 = 마지막 확인 시각)
    booked = {}  # room_id → booked_days_1m (예약 쿨다운 판정용; 0이면 공실)
    if not args.redo:
        rows = conn.execute(
            'SELECT room_id, collected_at, booked_days_1m FROM samsam_listings').fetchall()
        for r in rows:
            done.add(r[0]); coll[r[0]] = r[1] or ''; booked[r[0]] = r[2] or 0
        log(f"기존 적재: {len(done)}건 skip")

    log("매물 목록 수집 중...")
    rids = collect_rids(session)
    before_metro = len(rids)
    rids = {rid: room for rid, room in rids.items() if _room_sido(room) in METRO_SIDO}
    log(f"수도권(서울/경기/인천) 필터: {before_metro} → {len(rids)}건 (그 외 지역은 갱신 대상에서 제외)")

    targets = [(rid, room) for rid, room in rids.items() if rid not in done]
    refresh_targets = [rid for rid in rids if rid in done]

    # 시군구 사전필터: 목록 API의 province/state/주소로 상세 호출 전에 거른다(상세 호출 절감).
    sigungu_filter = args.sigungu.strip()
    if sigungu_filter:
        def _in_region(room):
            for k in ('province', 'state', 'addrLot', 'addrStreet'):
                if sigungu_filter in (room.get(k) or ''):
                    return True
            return False
        before = len(targets)
        targets = [(rid, room) for rid, room in targets if _in_region(room)]
        refresh_targets = [rid for rid in refresh_targets if _in_region(rids[rid])]
        log(f"시군구 필터 '{sigungu_filter}': {before} → {len(targets)}건")
    log(f"수집 대상: {len(targets)}건 (전체 {len(rids)}건)")
    if args.limit:
        log(f"--limit {args.limit} (적재 목표 건수)")

    # ── 샤딩 + 로테이션: 러너 IP별로 나누고, 가장 오래된 것부터 하루치만 갱신 ──────────
    shard_i, shard_n = _parse_shard()
    if shard_n > 1:
        before = len(refresh_targets)
        refresh_targets = [rid for rid in refresh_targets if rid % shard_n == shard_i]
        log(f"샤드 {shard_i}/{shard_n}: 예약률 갱신 대상 {before} → {len(refresh_targets)}건")
    # ── 공실 우선 로테이션 ──────────────────────────────────────────────────────
    # 이미 예약이 잡힌 매물은 (예: 한 달치 예약) 매일 다시 봐도 대개 그대로다. 그래서
    # 예약 있는 매물은 마지막 확인 후 BOOKED_COOLDOWN_DAYS일 동안 재갱신에서 빼고,
    # 공실(예약 0) 매물 위주로 자주 돌려 '새로 예약이 잡히는' 변화를 빨리 포착한다.
    # 쿨다운이 지난 예약 매물은 다시 대상에 들어와 예약 취소/연장도 놓치지 않는다.
    if BOOKED_COOLDOWN_DAYS > 0:
        cutoff = (datetime.now() - timedelta(days=BOOKED_COOLDOWN_DAYS)
                  ).isoformat(timespec='seconds')
        # collected_at은 timespec='seconds' ISO(예: 2026-07-02T22:16:20) — 같은 포맷이라
        # 문자열 비교로 시점 대소가 성립(파싱 예외 없이 안전). 빈값('')은 항상 cutoff 미만 = 대상.
        def _due(rid):
            if (booked.get(rid) or 0) <= 0:
                return True                      # 공실: 항상 갱신 대상
            return coll.get(rid, '') < cutoff    # 예약有: 마지막 확인이 쿨다운보다 오래됐을 때만
        before = len(refresh_targets)
        refresh_targets = [rid for rid in refresh_targets if _due(rid)]
        skipped = before - len(refresh_targets)
        log(f"공실 우선(쿨다운 {BOOKED_COOLDOWN_DAYS}일): 예약有 최근확인분 {skipped}건 제외 "
            f"→ {len(refresh_targets)}건 (공실+쿨다운경과)")
    # 오래된(stale) 순으로 정렬 → 매일 실행하면 자연스럽게 전체를 로테이션.
    refresh_targets.sort(key=lambda rid: coll.get(rid, ''))
    # 계정당 REFRESH_DAILY_LIMIT건씩 담당하므로, 하루 상한 = 한도 × 계정 수(계정 늘면 커버리지 배증).
    day_cap = REFRESH_DAILY_LIMIT * len(accounts) if REFRESH_DAILY_LIMIT else 0
    if day_cap and len(refresh_targets) > day_cap:
        log(f"로테이션: 오래된 순 {day_cap}건만 갱신"
            f"(계정 {len(accounts)}개×{REFRESH_DAILY_LIMIT}, 전체 {len(refresh_targets)}건, 나머지는 다음 실행)")
        refresh_targets = refresh_targets[:day_cap]

    batch, ok, fail = [], 0, 0
    for i, (rid, room) in enumerate(targets, 1):
        if args.limit and ok >= args.limit:
            break

        if i % 50 == 0:
            log(f"[시도{i}/{len(targets)}] 적재{ok} 실패{fail} skip{i - ok - fail}")

        detail = fetch_detail(session, rid)
        if not detail:
            fail += 1
            continue

        schedules, _ok = fetch_schedules(session, rid)
        row = map_row(rid, room, detail, schedules)
        batch.append(row)

        if len(batch) >= BATCH:
            upsert_batch(conn, batch)
            ok += len(batch)
            log(f"[시도{i}] {ok}건 적재")
            batch = []

        if i % 150 == 0:
            log("세션 갱신 중...")
            cookies = _get_cookies(email, pw)
            session = _make_session(cookies)

    if batch:
        upsert_batch(conn, batch)
        ok += len(batch)

    # 기존 매물(수도권) 예약률 갱신 — 상세는 그대로 두고 예약 스케줄만 다시 확인해
    # booked_days_*/blocked_days_1m을 최신화한다 (오르내림 추적용). 매물 수가 많아
    # 동시 요청(REFRESH_WORKERS)으로 처리하고, REFRESH_CHUNK건마다 세션을 새로 고친다.
    #
    # 결과를 메모리에 모아 두고(성공/실패/공실 구분), 전체 통과 후 한 번에 커밋한다.
    # 이렇게 해야 "성공했지만 예약 0인 공실"은 0으로 기록하되, "차단/에러로 못 받은 건"은
    # 건너뛰어 기존 값을 덮어쓰지 않는다. 또 전면 실패(응답은 오는데 데이터가 전무 = 소프트차단)
    # 시엔 0으로 도배해 DB를 오염시키지 않도록 커밋 자체를 막고 실패로 끝낸다.
    # 커밋 전략: 초반 EARLY_CHECK건까지는 버퍼에만 모아 두고 "차단 여부"를 먼저 판정한다.
    #   - 데이터 수신 0 → 레이트리밋/차단으로 보고 버퍼를 버린 뒤 즉시 실패 종료(0 오염 방지).
    #   - 데이터 확인됨 → 체크포인트 통과. 버퍼를 커밋하고, 이후로는 BATCH마다 증분 커밋한다.
    # 증분 커밋 덕에 실행 중에도 DB가 실시간으로 채워져(SELECT로 진행 관찰 가능) 크래시에도 안전하다.
    # 체크포인트 통과 후 도중에 차단이 나도, 실패건(ok=False)은 커밋에서 빠지므로 기존 값이 보존된다.
    # 계정을 여러 개 쓰면 대상을 계정 수만큼 연속 슬라이스로 나눠, 각 계정이 자기 몫(≤ 한도)만
    # 처리한다. 슬라이스가 계정 한도 아래라 한 계정이 도중에 소진될 일이 거의 없고, 소진되더라도
    # 그 계정 몫만 스킵하고 다음 계정으로 넘어간다(전체 실패 아님). 계정당 한도를 넘겨 처리하면
    # 체크포인트 통과 후 소진 시 빈값을 커밋할 수 있으므로, 슬라이스 크기(=REFRESH_DAILY_LIMIT)는
    # 실측 한도(~5000)보다 여유 있게 작게 유지한다.
    per_acct = REFRESH_DAILY_LIMIT or len(refresh_targets)
    log(f"기존 매물(수도권) 예약률 갱신 대상: {len(refresh_targets)}건, 동시 요청 {REFRESH_WORKERS}개, "
        f"계정 {len(accounts)}개(계정당 ≤{per_acct}건)")
    stats = {}
    buf, failed, total_data, processed, refreshed = [], 0, 0, 0, 0
    last_deploy = 0    # 마지막으로 배포(lab 커밋)한 시점의 refreshed 값
    any_data = False   # 어느 계정에서든 실제 스케줄 데이터를 하나라도 받았는지(전면차단 판정용)

    def _flush():
        nonlocal buf, refreshed
        if buf:
            update_schedules_batch(conn, buf)
            refreshed += len(buf)
            buf = []

    for acct_i in range(len(accounts)):
        slice_ = refresh_targets[acct_i * per_acct:(acct_i + 1) * per_acct]
        if not slice_:
            break
        email, pw = accounts[acct_i]
        if acct_i > 0:   # 첫 계정은 main 시작부에서 이미 로그인됨
            log(f"▶ 계정 {acct_i + 1}/{len(accounts)}({_mask(email)}) 로그인 — 담당 {len(slice_)}건")
            cookies = _get_cookies(email, pw)
            session = _make_session(cookies)
        else:
            log(f"▶ 계정 1/{len(accounts)}({_mask(email)}) 담당 {len(slice_)}건")

        checkpoint_passed = False
        with_data = 0      # 이 계정 세션의 체크포인트/차단 판정용(계정마다 한도가 따로)
        proc_acct = 0
        blocked = False

        for start in range(0, len(slice_), REFRESH_CHUNK):
            if blocked:
                break
            chunk = slice_[start:start + REFRESH_CHUNK]
            pool = ThreadPoolExecutor(max_workers=REFRESH_WORKERS)
            try:
                futs = {pool.submit(fetch_schedules, session, rid, stats): rid for rid in chunk}
                for fut in as_completed(futs):
                    rid = futs[fut]
                    processed += 1
                    proc_acct += 1
                    schedules, sched_ok = fut.result()
                    if not sched_ok:
                        failed += 1        # 차단/에러 → 신뢰 불가, 갱신 제외(기존 값 보존)
                    else:
                        # 성공: 빈 dict여도 '예약 0'이 확정된 공실이므로 반드시 기록
                        bk1 = _count_status(schedules, TODAY, D30, BOOKED_STATUSES)
                        bk2 = _count_status(schedules, TODAY, D60, BOOKED_STATUSES)
                        bk3 = _count_status(schedules, TODAY, D90, BOOKED_STATUSES)
                        bl1 = _count_status(schedules, TODAY, D30, BLOCKED_STATUSES)
                        mo = json.dumps(month_occ(schedules), ensure_ascii=False)
                        buf.append(
                            (bk1, bk2, bk3, bl1, mo, datetime.now().isoformat(timespec='seconds'), rid))
                        if schedules:
                            with_data += 1
                            total_data += 1
                            any_data = True
                        if checkpoint_passed and len(buf) >= BATCH:
                            _flush()   # 체크포인트 통과 후: 실시간 증분 커밋

                    # 체크포인트/차단 판정(이 계정 기준):
                    #   - 데이터가 조금이라도 확인되면(차단 아님) 바로 통과 → 실시간 증분 커밋 시작.
                    #   - 초반 EARLY_CHECK건까지 데이터 0이면 이 계정은 소진/차단 → 다음 계정으로.
                    if not checkpoint_passed:
                        if with_data >= 5:
                            checkpoint_passed = True
                            _flush()
                            log(f"체크포인트 통과(계정 {acct_i + 1}, {proc_acct}건, 데이터有 {with_data}) — 실시간 증분 커밋.")
                        elif proc_acct >= EARLY_CHECK and with_data == 0:
                            log(f"★ 계정 {acct_i + 1}/{len(accounts)}({_mask(email)}) 소진/차단: "
                                f"처음 {proc_acct}건 데이터 0 | HTTP {stats} — 미반영 버퍼 폐기, 다음 계정으로.")
                            blocked = True
                            break

                    if processed % 50 == 0:
                        log(f"[예약률 갱신 {processed}/{len(refresh_targets)}] "
                            f"DB반영 {refreshed}+버퍼 {len(buf)}(데이터有 {total_data}) 실패 {failed} | 계정 {acct_i + 1}")

                    # 점진 배포: DEPLOY_CHUNK건 DB 반영될 때마다 lab 재생성·커밋·push (CI에서만).
                    if DEPLOY_PUSH and refreshed - last_deploy >= DEPLOY_CHUNK:
                        deploy_lab(f"{refreshed}건 갱신")
                        last_deploy = refreshed
            finally:
                # 소진/차단 시엔 아직 시작 안 한 요청을 취소하고 기다리지 않는다(30분 블로킹 방지).
                pool.shutdown(wait=not blocked, cancel_futures=blocked)

            if not blocked and start + REFRESH_CHUNK < len(slice_):
                _flush()   # 세션 재로그인 전에 버퍼 비움(진행 보존)
                log("세션 갱신 중...")
                cookies = _get_cookies(email, pw)
                session = _make_session(cookies)

        if blocked:
            buf = []       # 이 계정의 미반영(빈값 오염) 버퍼 폐기 후 다음 계정으로
        else:
            _flush()       # 이 계정 정상 완료분 커밋

    # ── 종료 처리 ────────────────────────────────────────────────────
    conn.close()
    # 모든 계정이 데이터를 전혀 못 받았으면(전면 소진/차단) 0 오염 방지로 실패 종료.
    if not any_data and refresh_targets:
        log(f"★ 전 계정({len(accounts)}개) 스케줄 데이터 0(대상 {len(refresh_targets)}건) — 차단/한도, DB 미반영 종료.")
        log(f"  HTTP 응답 분포: {stats}")
        sys.exit(1)

    log(f"예약률 갱신 집계: DB반영 {refreshed}건(데이터有 {total_data}, 공실 {refreshed - total_data}) "
        f"/ 실패 {failed}건 / 대상 {len(refresh_targets)}건")
    log(f"  HTTP 응답 분포: {stats}")
    if refreshed - last_deploy > 0:
        deploy_lab(f"최종 {refreshed}건")   # 남은 갱신분 배포
    log(f"완료. 신규 적재 {ok}건 / 실패 {fail}건, 예약률 갱신(DB반영) {refreshed}건 / 실패 {failed}건")


if __name__ == '__main__':
    main()
