# -*- coding: utf-8 -*-
"""리브애니웨어(liveanywhere.me) 통합 채팅 — 수신 전용 provider.

삼삼(chat_auth.py, Firebase RTDB)과 달리 리브애니웨어 콘솔은 **자체 api-gateway REST +
httpOnly 쿠키(JWT: atoken/rtoken)** 구조이고, 채팅은 **Sendbird**를 api-gateway가 프록시한다.

정찰로 확인한 것(diary 2026-08-18):
- 로그인: account.liveanywhere.me 이메일+비번(OTP 없음) → 쿠키 atoken/rtoken 설정.
  (응답 바디엔 토큰 없음 — httpOnly 쿠키라 Playwright로 1회 로그인해 쿠키를 확보한다.)
- 갱신: POST api-gateway/v1/refresh-token (쿠키만, 브라우저 불필요).
- 채널: GET api-gateway/v1/sendbird/my-channels (쿠키만) → body.channels[].
  각 채널: sendbirdChannel{channelUrl, unreadMessageCount, lastMessage{createdAt,message},
  members[{nickname,userId,isMe}]}, accommodation{name}.

수신+알림만 담당 — 답장(전송)은 후속(Sendbird 전송 경로 필요).
"""
import json

import requests

ACCOUNT = "https://account.liveanywhere.me"
GW = "https://api-gateway.liveanywhere.me"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
# 인증에 실제로 쓰이는 쿠키(그 외 분석/광고 쿠키는 저장 안 함).
AUTH_COOKIE_NAMES = {"atoken", "rtoken", "rosetta-session", "rosetta-device"}


class LoginError(Exception):
    pass


def playwright_available():
    """로그인(브라우저 자동화) 가능 환경인지. 폴러가 로그인 못 하는 환경에서 상태를
    잘못 덮어쓰지 않도록 사전 체크(삼삼 chat_auth 와 동일 취지)."""
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _login_ui(pg, email, password):
    """account.liveanywhere.me 이메일 로그인 폼 조작. 로그인 후 /sign-in 이 남아있으면 실패."""
    pg.goto(f"{ACCOUNT}/sign-in", wait_until="networkidle", timeout=40000)
    pg.wait_for_timeout(1500)
    # '이메일/휴대폰 번호로 시작하기' 클릭해 폼 노출
    try:
        pg.get_by_text("이메일", exact=False).first.click(timeout=5000)
    except Exception:
        pass
    pg.wait_for_timeout(800)
    pg.locator("input#id, input[name=id], input[type=text]").first.fill(email)
    pg.locator("input[type=password]").first.fill(password)
    pg.get_by_role("button", name="로그인").first.click()
    pg.wait_for_timeout(4000)
    return "/sign-in" not in pg.url


def login_and_get_cookies(email, password):
    """Playwright로 로그인 → 인증 쿠키(atoken/rtoken 등) 리스트 반환.

    반환: [{'name','value','domain','path'}, ...] (인증 쿠키만). 실패 시 LoginError.
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True,
                              args=["--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(user_agent=UA, locale="ko-KR")
        pg = ctx.new_page()
        try:
            ok = _login_ui(pg, email, password)
            cookies = ctx.cookies()
        finally:
            b.close()
    if not ok:
        raise LoginError("로그인 실패 — 이메일/비밀번호를 확인해주세요.")
    auth = [
        {"name": c["name"], "value": c["value"],
         "domain": c["domain"], "path": c.get("path", "/")}
        for c in cookies
        if "liveanywhere" in c["domain"] and c["name"] in AUTH_COOKIE_NAMES
    ]
    if not any(c["name"] == "atoken" for c in auth):
        raise LoginError("로그인은 됐지만 인증 쿠키(atoken)를 받지 못했습니다.")
    return auth


def _session(cookies):
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "accept": "application/json"})
    for c in cookies:
        s.cookies.set(c["name"], c["value"],
                      domain=c["domain"].lstrip("."), path=c.get("path", "/"))
    return s


def refresh_cookies(cookies):
    """POST /v1/refresh-token 으로 세션 갱신(브라우저 불필요). 갱신된 인증 쿠키 리스트 반환.

    토큰 만료/무효 시 requests.HTTPError.
    """
    s = _session(cookies)
    r = s.post(f"{GW}/v1/refresh-token", timeout=15)
    r.raise_for_status()
    merged = {c["name"]: dict(c) for c in cookies}
    for ck in s.cookies:
        if ck.name in AUTH_COOKIE_NAMES:
            merged[ck.name] = {"name": ck.name, "value": ck.value,
                               "domain": ck.domain or ".liveanywhere.me",
                               "path": ck.path or "/"}
    return list(merged.values())


def list_channels(cookies):
    """GET /v1/sendbird/my-channels → 정규화된 채널(방) 리스트.

    각 원소: {channel_url, room_name, counterpart_nickname, counterpart_member,
              unread, last_message, last_message_time(epoch ms), last_message_id}
    인증 만료 시 requests.HTTPError(폴러가 잡아 재로그인).
    """
    s = _session(cookies)
    r = s.get(f"{GW}/v1/sendbird/my-channels", timeout=20)
    r.raise_for_status()
    body = (r.json() or {}).get("body") or {}
    out = []
    for ch in body.get("channels") or []:
        sb = ch.get("sendbirdChannel") or {}
        acc = ch.get("accommodation") or {}
        last = sb.get("lastMessage") or {}
        members = sb.get("members") or []
        # 상대(게스트) = 내가 아닌 멤버 중 첫 번째. (host=isMe=true)
        guest = next((m for m in members if not m.get("isMe")), None) or {}
        me = next((m for m in members if m.get("isMe")), None) or {}
        out.append({
            "channel_url": sb.get("channelUrl"),
            "room_name": acc.get("name") or sb.get("name"),
            "counterpart_nickname": guest.get("nickname"),
            "counterpart_member": str(guest.get("userId") or ""),
            "unread": sb.get("unreadMessageCount") or 0,
            "last_message": last.get("message"),
            "last_message_time": last.get("createdAt"),
            # 메시지 적재 dedupe 키. 게이트웨이엔 대화 이력 조회 API가 없어(404/500) 폴링 때
            # 보이는 lastMessage만 모을 수 있다 → messageId로 중복을 거른다.
            "last_message_id": str(last.get("messageId") or ""),
            "my_member": str(me.get("userId") or ""),
        })
    return out
