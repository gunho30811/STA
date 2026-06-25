# -*- coding: utf-8 -*-
"""
삼삼 건물유형 분류: list API(propertyTypes=APARTMENT/VILLA ...)로 rid별 유형 확보.
- 목록만 받음(스케줄/상세 X) → 가볍고 빠름. 단, 삼삼 레이트리밋 대비 천천히.
- 출력: data/btype_map.json  {rid: 'APARTMENT'|'VILLA'|...}
  (오피스텔은 officetel 캐시로 이미 확정이라 여기선 비오피스텔 유형만 수집)
"""
import json, os, time, sys, getpass
import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from datetime import datetime
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(BASE_DIR, "data")
OUT = os.path.join(DATA, "btype_map.json")
load_dotenv(os.path.join(BASE_DIR, ".env"))


def get_credentials():
    email = os.environ.get('SAMSAM_EMAIL') or input('삼삼엠투 이메일: ').strip()
    password = os.environ.get('SAMSAM_PASSWORD') or getpass.getpass('삼삼엠투 비밀번호: ')
    return email, password


EMAIL, PASSWORD = get_credentials()

BASE = 'https://web.33m2.co.kr'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
REQ_SLEEP = 0.6; BLOCK_WAIT = 120
PTYPES = ['APARTMENT', 'VILLA', 'HOUSE', 'STORE', 'OFFICE']   # 비오피스텔 후보(없는 유형은 0건)


def log(m): print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)
def headers():
    return {'User-Agent': UA, 'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'ko-KR,ko;q=0.9', 'Origin': BASE,
            'Referer': f'{BASE}/guest/room', 'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Dest': 'empty',
            'sec-ch-ua-platform': '"Windows"', 'sec-ch-ua-mobile': '?0'}


def get_cookies():
    for attempt in range(8):
        try:
            with sync_playwright() as p:
                b = p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
                ctx = b.new_context(user_agent=UA, locale='ko-KR'); pg = ctx.new_page()
                pg.goto(f'{BASE}/sign-in', wait_until='networkidle', timeout=40000)
                pg.wait_for_selector('input[type="email"], input[name="email"]', timeout=15000)
                pg.wait_for_timeout(500)

                email_input = pg.locator('input[type="email"], input[name="email"]').first
                email_input.click(); email_input.fill(''); email_input.type(EMAIL, delay=50)
                pw_input = pg.locator('input[type="password"]').first
                pw_input.click(); pw_input.fill(''); pw_input.type(PASSWORD, delay=50)
                pg.wait_for_timeout(300)

                pg.locator('button[type="submit"], button:has-text("로그인")').first.click()
                try:
                    pg.wait_for_url(lambda url: '/sign-in' not in url, timeout=10000)
                except Exception:
                    pg.wait_for_load_state('networkidle', timeout=8000)

                ok = '/sign-in' not in pg.url; cks = ctx.cookies(); b.close()
            if ok:
                log("로그인 성공"); return cks
            log(f"로그인 실패(잔류), 재시도 {attempt+1}")
        except Exception as e:
            log(f"로그인 예외({repr(e)[:60]}), 재시도 {attempt+1}"); time.sleep(3)
    raise RuntimeError("로그인 실패")


def main():
    s = requests.Session(); s.headers.update(headers())
    for c in get_cookies():
        s.cookies.set(c['name'], c['value'], domain=c['domain'].lstrip('.'))

    btype = {}
    for pt in PTYPES:
        page = 1; empty = 0; cnt = 0
        while page <= 400:
            url = f'{BASE}/v1/use-auth/rooms?propertyTypes={pt}&size=100&sortBy=POPULAR&page={page}'
            try:
                r = s.get(url, timeout=20)
            except Exception:
                time.sleep(2); continue
            time.sleep(REQ_SLEEP)
            if r.status_code == 403:
                log(f"403 차단 → {BLOCK_WAIT}s 대기"); time.sleep(BLOCK_WAIT); continue
            if r.status_code != 200:
                time.sleep(1); break
            d = r.json()
            if d.get('code') != 'SCSS_001':
                break
            content = d['data']['rooms'].get('content', [])
            if not content:
                empty += 1
                if empty >= 2: break
                page += 1; continue
            empty = 0
            for room in content:
                btype[str(room['rid'])] = pt
            cnt += len(content)
            if page % 10 == 0:
                log(f"  {pt}: {cnt}개...")
            page += 1
        log(f"{pt}: 총 {cnt}개")
        json.dump(btype, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False)  # 중간 저장

    json.dump(btype, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False)
    log(f"완료. 비오피스텔 rid {len(btype)}개 → {OUT}")
    from collections import Counter
    log(f"유형분포: {Counter(btype.values()).most_common()}")


if __name__ == "__main__":
    main()
