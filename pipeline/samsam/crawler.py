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
import argparse, getpass, json, os, re, subprocess, sys, threading, time
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

import db
from subway import stations_within

load_dotenv(os.path.join(BASE_DIR, '.env'))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ── 상수 ──────────────────────────────────────────────────────────────────────
BASE = 'https://web.33m2.co.kr'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
PROPERTY_TYPES = ['OFFICETEL', 'APARTMENT', 'VILLA', 'HOUSE', 'STORE', 'OFFICE']
BTYPE_KO = {
    'OFFICETEL': '오피스텔', 'APARTMENT': '아파트', 'VILLA': '빌라',
    'HOUSE': '주택', 'STORE': '상가', 'OFFICE': '사무실',
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


# ── 인증 ───────────────────────────────────────────────────────────────────────
def _get_credentials():
    email = os.environ.get('SAMSAM_EMAIL') or input('삼삼엠투 이메일: ').strip()
    pw = os.environ.get('SAMSAM_PASSWORD') or getpass.getpass('삼삼엠투 비밀번호: ')
    return email, pw


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
    'blocked_days_1m', 'station_500m_count', 'station_500m_names',
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
    """기존 매물의 예약 스케줄만 갱신 (rows: [(bk1, bk2, bk3, bl1, collected_at, room_id), ...])."""
    sql = ('UPDATE samsam_listings SET booked_days_1m=%s, booked_days_2m=%s, booked_days_3m=%s, '
           'blocked_days_1m=%s, collected_at=%s WHERE room_id=%s')
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


# ── 메인 ───────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--redo', action='store_true', help='기존 수집분 재수집')
    ap.add_argument('--sigungu', default='', help='시군구 필터 예) 강남구')
    args = ap.parse_args()

    email, pw = _get_credentials()
    cookies = _get_cookies(email, pw)
    session = _make_session(cookies)

    conn = db.connect()

    # 이미 적재된 room_id + 마지막 갱신 시각(로테이션에 사용)
    done = set()
    coll = {}   # room_id → collected_at (오래된 것부터 갱신하기 위한 정렬 키)
    if not args.redo:
        rows = conn.execute('SELECT room_id, collected_at FROM samsam_listings').fetchall()
        for r in rows:
            done.add(r[0]); coll[r[0]] = r[1] or ''
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
    # 오래된(stale) 순으로 정렬 → 매일 실행하면 자연스럽게 전체를 로테이션.
    refresh_targets.sort(key=lambda rid: coll.get(rid, ''))
    if REFRESH_DAILY_LIMIT and len(refresh_targets) > REFRESH_DAILY_LIMIT:
        log(f"로테이션: 오래된 순 {REFRESH_DAILY_LIMIT}건만 갱신(전체 {len(refresh_targets)}건, 나머지는 다음 실행)")
        refresh_targets = refresh_targets[:REFRESH_DAILY_LIMIT]

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
    log(f"기존 매물(수도권) 예약률 갱신 대상: {len(refresh_targets)}건, 동시 요청 {REFRESH_WORKERS}개")
    stats = {}
    buf, failed, with_data, processed, refreshed = [], 0, 0, 0, 0
    last_deploy = 0   # 마지막으로 배포(lab 커밋)한 시점의 refreshed 값
    checkpoint_passed = False
    rate_limited = False

    def _flush():
        nonlocal buf, refreshed
        if buf:
            update_schedules_batch(conn, buf)
            refreshed += len(buf)
            buf = []

    for start in range(0, len(refresh_targets), REFRESH_CHUNK):
        if rate_limited:
            break
        chunk = refresh_targets[start:start + REFRESH_CHUNK]
        with ThreadPoolExecutor(max_workers=REFRESH_WORKERS) as pool:
            futs = {pool.submit(fetch_schedules, session, rid, stats): rid for rid in chunk}
            for fut in as_completed(futs):
                rid = futs[fut]
                processed += 1
                schedules, sched_ok = fut.result()
                if not sched_ok:
                    failed += 1        # 차단/에러 → 신뢰 불가, 갱신 제외(기존 값 보존)
                else:
                    # 성공: 빈 dict여도 '예약 0'이 확정된 공실이므로 반드시 기록
                    bk1 = _count_status(schedules, TODAY, D30, BOOKED_STATUSES)
                    bk2 = _count_status(schedules, TODAY, D60, BOOKED_STATUSES)
                    bk3 = _count_status(schedules, TODAY, D90, BOOKED_STATUSES)
                    bl1 = _count_status(schedules, TODAY, D30, BLOCKED_STATUSES)
                    buf.append(
                        (bk1, bk2, bk3, bl1, datetime.now().isoformat(timespec='seconds'), rid))
                    if schedules:
                        with_data += 1
                    if checkpoint_passed and len(buf) >= BATCH:
                        _flush()   # 체크포인트 통과 후: 실시간 증분 커밋

                # 체크포인트 판정:
                #   - 데이터가 조금이라도 확인되면(차단 아님) 바로 통과 → 실시간 증분 커밋 시작.
                #     (로테이션으로 배치가 작을 때도 실시간 반영되게 하려고 EARLY_CHECK를 안 기다림.)
                #   - 초반 EARLY_CHECK건까지 데이터 0이면 차단으로 보고 중단(0 오염 방지).
                if not checkpoint_passed:
                    if with_data >= 5:
                        checkpoint_passed = True
                        _flush()
                        log(f"체크포인트 통과({processed}건, 데이터有 {with_data}) — 실시간 증분 커밋 시작.")
                    elif processed >= EARLY_CHECK and with_data == 0:
                        log(f"★ 조기 중단: 처음 {processed}건 중 데이터 수신 0건 "
                            f"(성공 {len(buf)} 실패 {failed}) | HTTP {stats} — 차단 판단, DB 미반영 종료.")
                        rate_limited = True
                        break

                if processed % 50 == 0:
                    log(f"[예약률 갱신 {processed}/{len(refresh_targets)}] "
                        f"DB반영 {refreshed}+버퍼 {len(buf)}(데이터有 {with_data}) 실패 {failed} | HTTP {stats}")

                # 점진 배포: DEPLOY_CHUNK건 DB 반영될 때마다 lab 재생성·커밋·push (CI에서만).
                if DEPLOY_PUSH and refreshed - last_deploy >= DEPLOY_CHUNK:
                    deploy_lab(f"{refreshed}건 갱신")
                    last_deploy = refreshed

        if not rate_limited and start + REFRESH_CHUNK < len(refresh_targets):
            _flush()   # 세션 재로그인 전에 버퍼 비움(진행 보존)
            log("세션 갱신 중...")
            cookies = _get_cookies(email, pw)
            session = _make_session(cookies)

    # ── 종료 처리 ────────────────────────────────────────────────────
    if rate_limited:
        conn.close()
        log(f"완료(실패). 신규 적재 {ok}건, 예약률 갱신 중단 — DB반영 {refreshed}건 / 실패 {failed}건")
        sys.exit(1)

    # 체크포인트를 못 넘겼는데(대상이 EARLY_CHECK보다 적음) 데이터가 전무하면 오염 방지로 미반영.
    if not checkpoint_passed and with_data == 0 and refresh_targets:
        conn.close()
        log(f"★ 예약데이터 수신 0건(대상 {len(refresh_targets)}건) — 차단 의심, DB 미반영 종료.")
        log(f"  HTTP 응답 분포: {stats}")
        sys.exit(1)

    _flush()   # 남은 버퍼 커밋
    conn.close()
    log(f"예약률 갱신 집계: DB반영 {refreshed}건(데이터有 {with_data}, 공실 {refreshed - with_data}) "
        f"/ 실패 {failed}건 / 대상 {len(refresh_targets)}건")
    log(f"  HTTP 응답 분포: {stats}")
    if refreshed - last_deploy > 0:
        deploy_lab(f"최종 {refreshed}건")   # 남은 갱신분 배포
    log(f"완료. 신규 적재 {ok}건 / 실패 {fail}건, 예약률 갱신(DB반영) {refreshed}건 / 실패 {failed}건")


if __name__ == '__main__':
    main()
