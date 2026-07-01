# -*- coding: utf-8 -*-
"""삼삼엠투 채팅(Firebase RTDB) 인증.

계정 연결 시 Playwright로 1회 로그인해 Firebase refreshToken을 얻는다.
그 이후로는 순수 HTTP(requests)만으로 idToken을 갱신할 수 있어 폴링 때
브라우저가 필요 없다 (정찰로 확인된 구조).

필요 환경변수 없음 — email/password는 호출부(웹 계정연결 폼, 폴링 파이프라인)에서 전달.
"""
import base64
import json

import requests

BASE = 'https://web.33m2.co.kr'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
FIREBASE_API_KEY = 'AIzaSyCITSQcxSCIBxJfFHTtYznNKcl5QD8mkU4'
RTDB_BASE = 'https://spacev-33m2-rdb-default-rtdb.firebaseio.com'


class LoginError(Exception):
    pass


def _member_id_from_id_token(id_token):
    """idToken(JWT) payload에서 user_id(=삼삼 member_id) 추출. 서명 검증 없음(신뢰 응답 파싱용)."""
    payload_b64 = id_token.split('.')[1]
    padded = payload_b64 + '=' * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded))
    return payload.get('user_id') or payload.get('sub')


def login_and_get_refresh_token(email, password):
    """Playwright로 삼삼엠투 로그인 → Firebase refreshToken + member_id 획득.

    반환: {'refresh_token', 'id_token', 'samsam_member_id'}
    실패 시 LoginError.
    """
    # 지연 import: Vercel 등 서버리스 환경엔 브라우저 자동화가 없어 모듈 최상단에서
    # import하면 이 함수를 안 써도(REST 폴링만 해도) 앱 전체가 기동 실패한다.
    from playwright.sync_api import sync_playwright

    captured = {}

    def on_response(resp):
        try:
            if 'identitytoolkit.googleapis.com' in resp.url and 'signInWithCustomToken' in resp.url:
                captured['signin'] = resp.json()
        except Exception:
            pass

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
        ctx = b.new_context(user_agent=UA, locale='ko-KR')
        pg = ctx.new_page()
        pg.on('response', on_response)
        try:
            pg.goto(f'{BASE}/sign-in', wait_until='networkidle', timeout=40000)
            pg.wait_for_selector('input[type="email"], input[name="email"]', timeout=15000)
            pg.wait_for_timeout(500)

            email_input = pg.locator('input[type="email"], input[name="email"]').first
            email_input.click(); email_input.fill(''); email_input.type(email, delay=50)
            pw_input = pg.locator('input[type="password"]').first
            pw_input.click(); pw_input.fill(''); pw_input.type(password, delay=50)
            pg.wait_for_timeout(300)

            btn = pg.locator('button[type="submit"], button:has-text("로그인")').first
            btn.click()
            try:
                pg.wait_for_url(lambda url: '/sign-in' not in url, timeout=10000)
            except Exception:
                pg.wait_for_load_state('networkidle', timeout=8000)
            pg.wait_for_timeout(1500)

            ok = '/sign-in' not in pg.url
        finally:
            b.close()

    if not ok or 'signin' not in captured:
        raise LoginError('로그인 실패 — 이메일/비밀번호를 확인해주세요.')

    sign = captured['signin']
    refresh_token = sign.get('refreshToken')
    id_token = sign.get('idToken')
    if not refresh_token or not id_token:
        raise LoginError('로그인은 됐지만 인증 토큰을 받지 못했습니다.')

    return {
        'refresh_token': refresh_token,
        'id_token': id_token,
        'samsam_member_id': _member_id_from_id_token(id_token),
    }


def refresh_id_token(refresh_token):
    """refreshToken → 새 idToken. 브라우저 불필요(HTTP 1회).

    반환: {'id_token', 'refresh_token'(갱신값), 'samsam_member_id'}
    토큰 만료/무효 시 requests.HTTPError.
    """
    r = requests.post(
        f'https://securetoken.googleapis.com/v1/token?key={FIREBASE_API_KEY}',
        data={'grant_type': 'refresh_token', 'refresh_token': refresh_token},
        timeout=15,
    )
    r.raise_for_status()
    tok = r.json()
    return {
        'id_token': tok['id_token'],
        'refresh_token': tok['refresh_token'],
        'samsam_member_id': tok['user_id'],
    }


def rtdb_get(path, id_token, **extra_params):
    """RTDB REST 조회. path 예: 'live/chatlist/7010' (확장자·auth 파라미터는 내부 처리).

    extra_params로 orderBy='"message_time"', limitToLast=50 등 RTDB 쿼리 파라미터 전달 가능.
    """
    params = {'auth': id_token, **extra_params}
    r = requests.get(f'{RTDB_BASE}/{path}.json', params=params, timeout=20)
    r.raise_for_status()
    return r.json()
