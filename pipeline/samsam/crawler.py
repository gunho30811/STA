# -*- coding: utf-8 -*-
"""
삼삼엠투 전체 매물 크롤러 → Supabase samsam_listings 적재.

사용법:
  python pipeline/samsam/crawler.py              # 전체 수집 (이어받기)
  python pipeline/samsam/crawler.py --limit 50   # N건만 (테스트)
  python pipeline/samsam/crawler.py --redo       # 기존 수집분 재수집

필요 환경변수 (.env):
  DATABASE_URL, SAMSAM_EMAIL, SAMSAM_PASSWORD
"""
import argparse, getpass, json, os, re, sys, time
from collections import defaultdict
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
    """전체 매물 rid 목록 수집. {rid: property_type} 반환."""
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
                rids[room['rid']] = pt
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
    """예약 스케줄. 엔드포인트: GET /v1/use-auth/rooms/{rid}/schedules"""
    d = _get(session, f'{BASE}/v1/use-auth/rooms/{rid}/schedules',
             params={'from': TODAY.isoformat(), 'to': D90.isoformat()})
    time.sleep(REQ_SLEEP)
    if not d:
        return {}
    data = d.get('data', {})
    # 응답 형태: {schedules: {...}} 또는 {calendars: {...}} 또는 직접 dict
    return (data.get('schedules') or data.get('calendars') or
            data.get('schedule') or {})


# ── 행 매핑 ────────────────────────────────────────────────────────────────────
def map_row(rid, pt, detail, schedules):
    """API 응답 → samsam_listings 컬럼 dict."""
    road_raw = detail.get('roadAddress') or detail.get('road_address') or ''
    jibun_raw = detail.get('jibunAddress') or detail.get('jibun_address') or ''

    floor = (detail.get('floor')
             or _parse_floor(road_raw)
             or _parse_floor(jibun_raw))
    road_addr = _strip_floor(road_raw)
    jibun_addr = _strip_floor(jibun_raw)
    bldg_name = (detail.get('buildingName') or detail.get('building_name')
                 or _parse_building_name(jibun_raw))

    lat = detail.get('lat') or detail.get('latitude')
    lng = detail.get('lng') or detail.get('longitude')

    area_m2 = (detail.get('exclusiveArea') or detail.get('area_m2')
               or detail.get('area') or detail.get('supplyArea'))
    area_py = detail.get('pyeong') or detail.get('area_pyeong')
    if area_m2 and not area_py:
        area_py = round(area_m2 / 3.305785)

    rent_w = (detail.get('fee') or detail.get('rentFee')
              or detail.get('rent_weekly') or 0)
    mgmt_w = (detail.get('maintenanceFee') or detail.get('maintenance_fee')
              or detail.get('maintenance_weekly') or 0)
    total_w = (detail.get('totalFee') or detail.get('total_fee')
               or rent_w + mgmt_w)

    jibun_ref = jibun_raw or road_raw
    sido = _parse_sido(jibun_ref)
    sigungu = _parse_sigungu(jibun_ref)
    dong = _parse_dong(jibun_ref)

    booked = {'booking'}
    blocked = {'disable', 'disabled', 'blocked'}
    bk1 = _count_status(schedules, TODAY, D30, booked)
    bk2 = _count_status(schedules, TODAY, D60, booked)
    bk3 = _count_status(schedules, TODAY, D90, booked)
    bl1 = _count_status(schedules, TODAY, D30, blocked)

    sub500 = sub1k = []
    if lat and lng:
        sub500 = stations_within(lat, lng, 500)
        sub1k = stations_within(lat, lng, 1000)

    basic = detail.get('basicOptions') or detail.get('basic_options') or []
    extra = detail.get('extraOptions') or detail.get('extra_options') or []

    return {
        'room_id': rid,
        'url': f'{BASE}/guest/room/{rid}',
        'name': detail.get('name') or detail.get('title') or '',
        'building_type': BTYPE_KO.get(pt, pt),
        'road_address': road_addr,
        'jibun_address': jibun_addr,
        'building_name': bldg_name,
        'floor': floor,
        'lat': lat,
        'lng': lng,
        'area_m2': area_m2,
        'area_pyeong': area_py,
        'rooms': (detail.get('roomCount') or detail.get('rooms') or 1),
        'bathrooms': (detail.get('bathroomCount') or detail.get('bathrooms') or 0),
        'kitchens': (detail.get('kitchenCount') or detail.get('kitchens') or 0),
        'living_rooms': (detail.get('livingRoomCount') or detail.get('living_rooms') or 0),
        'elevator': bool(detail.get('hasElevator') or detail.get('elevator')),
        'parking': bool(detail.get('canParking') or detail.get('hasParking')
                        or detail.get('parking')),
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
    targets = [(rid, pt) for rid, pt in rids.items() if rid not in done]
    log(f"수집 대상: {len(targets)}건 (전체 {len(rids)}건)")

    if args.limit:
        targets = targets[:args.limit]
        log(f"--limit {args.limit} 적용")

    sigungu_filter = args.sigungu.strip()
    if sigungu_filter:
        log(f"시군구 필터: {sigungu_filter}")

    batch, ok, fail = [], 0, 0
    for i, (rid, pt) in enumerate(targets, 1):
        detail = fetch_detail(session, rid)
        if not detail:
            fail += 1
            if i % 100 == 0:
                log(f"[{i}/{len(targets)}] ok={ok} fail={fail}")
            continue

        # 시군구 필터: 도로명/지번 주소에 포함 여부 체크
        if sigungu_filter:
            road = detail.get('roadAddress') or detail.get('road_address') or ''
            jibun = detail.get('jibunAddress') or detail.get('jibun_address') or ''
            if sigungu_filter not in road and sigungu_filter not in jibun:
                continue

        schedules = fetch_schedules(session, rid)
        row = map_row(rid, pt, detail, schedules)
        batch.append(row)

        if len(batch) >= BATCH:
            upsert_batch(conn, batch)
            ok += len(batch)
            log(f"[{i}/{len(targets)}] {ok}건 적재")
            batch = []

        if i % 150 == 0:
            log("세션 갱신 중...")
            cookies = _get_cookies(email, pw)
            session = _make_session(cookies)

    if batch:
        upsert_batch(conn, batch)
        ok += len(batch)

    conn.close()
    log(f"완료. 적재 {ok}건 / 실패 {fail}건")


if __name__ == '__main__':
    main()
