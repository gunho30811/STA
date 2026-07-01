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
import argparse, getpass, json, os, re, sys, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, 'pipeline', 'naver'))

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
REFRESH_WORKERS = 4       # 기존 매물 예약률 갱신 동시 요청 수
REFRESH_CHUNK = 2000      # 이 건수마다 세션(로그인)을 새로 고침

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


# ── API 호출 ───────────────────────────────────────────────────────────────────
def _get(session, url, params=None):
    """GET 요청 — 403이면 BLOCK_WAIT 후 1회 재시도, 실패 시 None 반환."""
    try:
        r = session.get(url, params=params, timeout=15)
    except Exception:
        return None
    if r.status_code == 403:
        log(f"403 차단, {BLOCK_WAIT}s 대기")
        time.sleep(BLOCK_WAIT)
        try:
            r = session.get(url, params=params, timeout=15)
        except Exception:
            return None
    if r.status_code != 200:
        return None
    d = r.json()
    return d if d.get('code') == 'SCSS_001' else None


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


def fetch_schedules(session, rid):
    """예약 스케줄 → {날짜:상태} dict. 상태: 'booking'(예약)/'disable'(막힘).

    엔드포인트는 year+month(정수) 필수, 응답은 data.schedules = [{date,status}, ...].
    오늘~+90일을 덮는 월(보통 3~4개)을 각각 호출해 병합.
    """
    out = {}
    months = {(TODAY.year, TODAY.month)}
    for off in (30, 60, 90):
        dd = TODAY + timedelta(days=off)
        months.add((dd.year, dd.month))
    for (y, m) in sorted(months):
        d = _get(session, f'{BASE}/v1/use-auth/rooms/{rid}/schedules',
                 params={'year': y, 'month': m})
        time.sleep(REQ_SLEEP)
        if not d:
            continue
        for e in (d.get('data', {}).get('schedules') or []):
            if e.get('date'):
                out[e['date']] = e.get('status')
    return out


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

    # 이미 적재된 room_id
    done = set()
    if not args.redo:
        rows = conn.execute('SELECT room_id FROM samsam_listings').fetchall()
        done = {r[0] for r in rows}
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

        schedules = fetch_schedules(session, rid)
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
    log(f"기존 매물(수도권) 예약률 갱신 대상: {len(refresh_targets)}건, 동시 요청 {REFRESH_WORKERS}개")
    upd_batch, refreshed, processed = [], 0, 0
    for start in range(0, len(refresh_targets), REFRESH_CHUNK):
        chunk = refresh_targets[start:start + REFRESH_CHUNK]
        with ThreadPoolExecutor(max_workers=REFRESH_WORKERS) as pool:
            futs = {pool.submit(fetch_schedules, session, rid): rid for rid in chunk}
            for fut in as_completed(futs):
                rid = futs[fut]
                processed += 1
                schedules = fut.result()
                if schedules:
                    bk1 = _count_status(schedules, TODAY, D30, BOOKED_STATUSES)
                    bk2 = _count_status(schedules, TODAY, D60, BOOKED_STATUSES)
                    bk3 = _count_status(schedules, TODAY, D90, BOOKED_STATUSES)
                    bl1 = _count_status(schedules, TODAY, D30, BLOCKED_STATUSES)
                    upd_batch.append(
                        (bk1, bk2, bk3, bl1, datetime.now().isoformat(timespec='seconds'), rid))
                    if len(upd_batch) >= BATCH:
                        update_schedules_batch(conn, upd_batch)
                        refreshed += len(upd_batch)
                        upd_batch = []
                if processed % 500 == 0:
                    log(f"[예약률 갱신 {processed}/{len(refresh_targets)}] {refreshed}건 갱신")

        if start + REFRESH_CHUNK < len(refresh_targets):
            log("세션 갱신 중...")
            cookies = _get_cookies(email, pw)
            session = _make_session(cookies)

    if upd_batch:
        update_schedules_batch(conn, upd_batch)
        refreshed += len(upd_batch)

    conn.close()
    log(f"완료. 신규 적재 {ok}건 / 실패 {fail}건, 예약률 갱신 {refreshed}건")


if __name__ == '__main__':
    main()
