# -*- coding: utf-8 -*-
"""삼삼엠투 통합 채팅 폴링.

계정별로 idToken 갱신(HTTP만 사용, 브라우저 불필요) → RTDB chatlist/messagelist 조회
→ DB 적재. refreshToken 만료 시 저장된 비밀번호로 재로그인(Playwright, 이때만 브라우저 사용).

사용법:
  python pipeline/samsam/chat_poll.py

필요 환경변수(.env): DATABASE_URL, CHAT_ENC_KEY
"""
import datetime
import os
import sys

# 이 파일은 common/ 아래 → 레포 루트는 한 단계 위(db 등은 루트에 있음).
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)                                    # db(루트)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # chat_auth·crypto_util(common)

from dotenv import load_dotenv

load_dotenv(os.path.join(BASE_DIR, '.env'))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import chat_auth
import chat_liveanywhere as chat_la
import crypto_util
import db
import kakao_notify
import json as _json

MSG_LIMIT = 50  # 폴링마다 방당 최근 메시지 N개만 조회 — 신규분만 있으면 충분, 전체 이력 아님
CHAT_URL = f"https://{os.environ.get('RENDIT_DOMAIN', 'rendits.duckdns.org')}/samsam/chat/"


def log(m):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {m}", flush=True)


def _now():
    return datetime.datetime.now().isoformat(timespec='seconds')


def _mark_status(conn, acct_id, status, error):
    conn.execute(
        "UPDATE samsam_accounts SET status=%s, last_error=%s, last_polled_at=%s WHERE id=%s",
        (status, error, _now(), acct_id))
    conn.commit()


def _upsert_room(conn, acct_id, room_key, room, nickname):
    row = conn.execute(
        """INSERT INTO samsam_chat_rooms
           (account_id, samsam_room_key, room_name, host_or_guest, counterpart_member,
            counterpart_nickname, contract_status, chat_room_status, start_date, end_date,
            last_message, last_message_time, updated_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (account_id, samsam_room_key) DO UPDATE SET
             room_name=EXCLUDED.room_name, host_or_guest=EXCLUDED.host_or_guest,
             counterpart_member=EXCLUDED.counterpart_member,
             counterpart_nickname=EXCLUDED.counterpart_nickname,
             contract_status=EXCLUDED.contract_status, chat_room_status=EXCLUDED.chat_room_status,
             start_date=EXCLUDED.start_date, end_date=EXCLUDED.end_date,
             last_message=EXCLUDED.last_message, last_message_time=EXCLUDED.last_message_time,
             updated_at=EXCLUDED.updated_at
           RETURNING id""",
        (acct_id, str(room_key), room.get('room_name'), room.get('host_or_guest'),
         str(room.get('member') or ''), nickname, room.get('contract_status'),
         room.get('chat_room_status'), room.get('start_date'), room.get('end_date'),
         room.get('last_message'), room.get('last_message_time'), _now()),
    ).fetchone()
    conn.commit()
    return row[0]


def _get_nickname(id_token, member_id, cache):
    """RTDB live/users/{id}에서 닉네임 조회. 순수 HTTP라 Vercel 1분 cron에서도 바로 동작.
    같은 계정 폴링 1회 안에서 상대가 겹칠 수 있어 cache로 중복 조회 방지."""
    if member_id in cache:
        return cache[member_id]
    nickname = None
    try:
        user = chat_auth.rtdb_get(f'live/users/{member_id}', id_token) or {}
        nickname = user.get('nickname')
    except Exception as e:
        log(f"    상대(member {member_id}) 닉네임 조회 실패: {repr(e)[:80]}")
    cache[member_id] = nickname
    return nickname


def _poll_messages(conn, room_id, room_key, id_token):
    try:
        msgs = chat_auth.rtdb_get(
            f'live/messagelist/{room_key}', id_token,
            orderBy='"message_time"', limitToLast=MSG_LIMIT) or {}
    except Exception as e:
        log(f"    room {room_key} 메시지 조회 실패: {repr(e)[:80]}")
        return
    rows = [
        (room_id, k, str(m.get('sender') or ''), str(m.get('receiver') or ''),
         m.get('message'), m.get('message_type'), m.get('message_time'),
         m.get('image'), m.get('title'))
        for k, m in msgs.items()
    ]
    if not rows:
        return
    conn.executemany(
        """INSERT INTO samsam_chat_messages
           (room_id, msg_key, sender, receiver, message, message_type, message_time, image, title)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (room_id, msg_key) DO NOTHING""",
        rows)
    conn.commit()


def _notify_new_chat(conn, acct, room_id, room, nickname, st, prev_in, new_in, first_poll):
    """방별 첫 신규 수신 메시지에만 카톡 알림. '읽음→안읽음' 전환 순간 1회.

    st: {last_read_at, last_notified_time}(적재 전 상태), prev_in/new_in: 적재 전/후 상대방 최신 시각.
    first_poll(계정 첫 연결)엔 기준선만 잡고 알림 안 함(기존 대화 무더기 알림 방지)."""
    if new_in is None:
        return
    has_new = prev_in is None or new_in > prev_in
    if not has_new:
        return
    last_read = (st['last_read_at'] if st else None) or 0
    last_notif = (st['last_notified_time'] if st else None) or 0
    unread_before = prev_in is not None and prev_in > last_read
    should = (not first_poll) and (not unread_before) and (new_in > last_notif)
    if should:
        label = acct.get('label') or acct.get('samsam_email') or '삼삼 계정'
        preview = (room.get('last_message') or '').strip().replace('\n', ' ')[:60]
        text = (f"[rendit] 새 채팅 문의\n계정: {label}\n"
                f"{nickname or '게스트'}: {preview or '(내용 없음)'}")
        try:
            if kakao_notify.send_to_member(conn, acct['member_id'], text, CHAT_URL, "채팅 열기"):
                log(f"    카톡 알림 발송(room {room_id}, member#{acct['member_id']})")
        except Exception as e:
            log(f"    카톡 알림 오류: {repr(e)[:100]}")
    if new_in > last_notif:
        conn.execute("UPDATE samsam_chat_rooms SET last_notified_time=%s WHERE id=%s",
                     (new_in, room_id))
        conn.commit()


def poll_account(conn, acct):
    acct_id = acct['id']
    # 재연결 필요(비번 오류 등)로 표시된 계정은 자동 폴링에서 제외 — 1분 크론이 실패하는
    # 브라우저 로그인을 무한 반복하지 않게. 사용자가 웹에서 수동 폴링/재연결하면 다시 시도.
    if (acct.get('status') or '') == 'reauth_needed':
        log(f"  계정#{acct_id} 재연결 필요 상태 — 자동 재시도 생략")
        return
    first_poll = not acct['refresh_token_enc']  # 계정 첫 연결(아직 refreshToken 없음)
    tok = None

    # refresh_token_enc가 아직 없으면(웹에서 Playwright 없이 큐잉만 된 pending_login 계정)
    # 최초 로그인부터 시도. 있으면 순수 HTTP 갱신을 먼저 시도하고, 실패하면 같은 재로그인
    # 경로(비번 기반)로 폴백 — 최초 연결과 만료 재연결을 한 경로로 처리.
    if acct['refresh_token_enc']:
        try:
            tok = chat_auth.refresh_id_token(crypto_util.decrypt(acct['refresh_token_enc']))
        except Exception as e:
            log(f"  계정#{acct_id} refreshToken 만료 추정({repr(e)[:60]}) — 저장된 비번으로 재로그인 시도")
    else:
        log(f"  계정#{acct_id} 최초 로그인 대기 중 — 로그인 시도")

    if tok is None:
        if not acct['password_enc']:
            _mark_status(conn, acct_id, 'reauth_needed', '저장된 비밀번호 없음 — 재연결 필요')
            return
        if not chat_auth.playwright_available():
            # Vercel(1분 cron)처럼 브라우저 자동화가 없는 환경 — 여기서 시도하면 항상 실패해
            # 상태를 잘못 덮어쓴다. 그냥 스킵하고 GH Actions(Playwright 설치됨, 10분 주기)에 맡긴다.
            log(f"  계정#{acct_id} 로그인 필요하지만 이 환경엔 Playwright 없음 — 스킵")
            return
        try:
            password = crypto_util.decrypt(acct['password_enc'])
            tok = chat_auth.login_and_get_refresh_token(acct['samsam_email'], password)
        except Exception as e2:
            log(f"  계정#{acct_id} 로그인 실패: {repr(e2)[:100]}")
            _mark_status(conn, acct_id, 'reauth_needed', repr(e2)[:200])
            return

    id_token = tok['id_token']
    member_id = tok['samsam_member_id']

    try:
        chatlist = chat_auth.rtdb_get(f'live/chatlist/{member_id}', id_token) or {}
    except Exception as e:
        log(f"  계정#{acct_id} chatlist 조회 실패: {repr(e)[:100]}")
        _mark_status(conn, acct_id, 'error', repr(e)[:200])
        return

    # 임대인(host) 모드 채팅만 저장 — 이 계정이 게스트로 예약한 방(임차인 채팅)은 제외.
    host_rooms = {k: r for k, r in chatlist.items() if r.get('host_or_guest') == 'host'}
    nickname_cache = {}
    me = str(member_id)
    for room_key, room in host_rooms.items():
        nickname = None
        counterpart = room.get('member')
        if counterpart:
            nickname = _get_nickname(id_token, counterpart, nickname_cache)
        room_id = _upsert_room(conn, acct_id, room_key, room, nickname)
        # 신규 수신 메시지 감지용 상태(적재 전) — 상대방(sender != 내 member_id) 최신 메시지 시각.
        st = conn.execute(
            "SELECT last_read_at, last_notified_time FROM samsam_chat_rooms WHERE id=%s",
            (room_id,)).fetchone()
        prev_in = conn.execute(
            "SELECT MAX(message_time) FROM samsam_chat_messages WHERE room_id=%s AND sender<>%s",
            (room_id, me)).fetchone()[0]
        _poll_messages(conn, room_id, room_key, id_token)
        new_in = conn.execute(
            "SELECT MAX(message_time) FROM samsam_chat_messages WHERE room_id=%s AND sender<>%s",
            (room_id, me)).fetchone()[0]
        _notify_new_chat(conn, acct, room_id, room, nickname, st, prev_in, new_in, first_poll)

    conn.execute(
        "UPDATE samsam_accounts SET refresh_token_enc=%s, samsam_member_id=%s, "
        "status='ok', last_error=NULL, last_polled_at=%s WHERE id=%s",
        (crypto_util.encrypt(tok['refresh_token']), str(member_id), _now(), acct_id))
    conn.commit()
    log(f"  계정#{acct_id}({acct['label'] or acct['samsam_email']}) "
        f"임대인 채팅방 {len(host_rooms)}개 갱신(전체 {len(chatlist)}개 중 게스트모드 제외)")


def _mark_outbox(conn, outbox_id, status, error=None):
    conn.execute(
        "UPDATE samsam_chat_outbox SET status=%s, last_error=%s, sent_at=%s WHERE id=%s",
        (status, error, _now(), outbox_id))
    conn.commit()


def _verify_room_live(item):
    """발송 직전, 삼삼 실서버(RTDB)로 방이 살아있고 지정한 상대의 방이 맞는지 교차확인.

    UI 조작(send_message)만으로는 같은 매물명 방이 여러 개일 때 오발송 위험이 있어, 그 전에
    기계적으로 확실한 신원(room_key·상대 회원ID)으로 한 겹 더 막는다.

    반환: (ok: bool, reason: str)
      - 방이 채팅목록에서 사라졌으면(삭제) → 발송 보류. 사용자가 원한 정책:
        "삭제된 방엔 상대가 다시 연락할 때까지 발송하지 않는다".
      - 방의 상대 회원ID가 DB와 다르면(신원 불일치) → 오발송 방지 위해 중단.
    """
    enc = item.get('refresh_token_enc')
    if not enc:
        return False, '계정 토큰 없음 — 재연결 필요'
    try:
        tok = chat_auth.refresh_id_token(crypto_util.decrypt(enc))
        id_token, me = tok['id_token'], tok['samsam_member_id']
    except Exception as e:
        return False, f'토큰 갱신 실패: {repr(e)[:80]}'
    try:
        chatlist = chat_auth.rtdb_get(f'live/chatlist/{me}', id_token) or {}
    except Exception as e:
        return False, f'chatlist 조회 실패: {repr(e)[:80]}'
    room = chatlist.get(str(item['samsam_room_key']))
    if not room:
        return False, '방이 삭제됨(채팅목록에 없음) — 상대가 다시 연락할 때까지 발송 보류'
    if str(room.get('member') or '') != str(item['counterpart_member'] or ''):
        return False, ('방-상대 불일치(목록 상대 '
                       f"{room.get('member')} != DB {item['counterpart_member']}) — 발송 중단")
    if room.get('host_or_guest') != 'host':
        return False, '임대인(host) 방이 아님 — 발송 중단'
    return True, 'ok'


def process_outbox(conn):
    """대기 중인 답장(samsam_chat_outbox status='pending')을 실제 발송.

    로그인과 마찬가지로 브라우저 자동화(Playwright)가 필요해 GH Actions에서만 처리한다
    (Vercel 1분 cron은 poll_all만 돌리고 여긴 안 건드림 — chat_api_cron_poll이 이 함수를
    호출하지 않는 이유).

    오발송 방지가 최우선이라 여러 겹으로 확인한다:
      1) RTDB 교차확인(_verify_room_live): room_key 생존 + 상대 회원ID 일치.
      2) send_message: 매물명 + 상대 닉네임으로 방을 '정확히 1개' 특정 + 열린 방 재확인.
    어느 겹이라도 어긋나면 발송하지 않는다. 삭제된 방은 'blocked'로 남겨(=재시도 안 함)
    상대가 다시 연락해 방이 되살아나기 전까지 보내지 않는다.
    """
    if not chat_auth.playwright_available():
        return 0
    items = conn.execute(
        """SELECT o.id, o.room_id, o.message,
                  r.room_name, r.samsam_room_key, r.counterpart_member, r.counterpart_nickname,
                  a.samsam_email, a.password_enc, a.refresh_token_enc
           FROM samsam_chat_outbox o
           JOIN samsam_chat_rooms r ON r.id = o.room_id
           JOIN samsam_accounts a ON a.id = r.account_id
           WHERE o.status='pending'"""
    ).fetchall()
    for item in items:
        item = dict(item)
        if not item['password_enc']:
            _mark_outbox(conn, item['id'], 'failed', '연결 계정 비밀번호 없음')
            continue
        # 안전장치 ①: 발송 전 방 생존·상대 신원 교차확인(삭제된 방은 보류).
        ok, reason = _verify_room_live(item)
        if not ok:
            _mark_outbox(conn, item['id'], 'blocked', reason)
            log(f"  outbox#{item['id']} 발송 보류: {reason}")
            continue
        # 안전장치 ②: UI에서 매물명+닉네임으로 방을 정확히 특정해 발송(내부에서 재확인).
        try:
            password = crypto_util.decrypt(item['password_enc'])
            chat_auth.send_message(
                item['samsam_email'], password,
                room_name=item['room_name'], message=item['message'],
                counterpart_nickname=item['counterpart_nickname'],
                samsam_room_key=item['samsam_room_key'])
            _mark_outbox(conn, item['id'], 'sent')
            log(f"  outbox#{item['id']} 방({item['room_name']}/{item['counterpart_nickname']}) 발송 완료")
        except Exception as e:
            log(f"  outbox#{item['id']} 발송 실패: {repr(e)[:120]}")
            _mark_outbox(conn, item['id'], 'failed', repr(e)[:200])
    return len(items)


def poll_liveanywhere_account(conn, acct):
    """리브애니웨어 수신 폴러 — 쿠키 세션으로 Sendbird 채널(my-channels) 조회 →
    신규 수신 메시지에 카톡 알림. 답장(전송)은 미지원(수신 전용).

    인증은 httpOnly 쿠키(atoken/rtoken)라 refresh_token_enc 에 쿠키 번들(JSON)을 암호화 저장.
    갱신은 순수 HTTP(refresh_cookies), 만료 시 저장 비번으로 재로그인(Playwright)."""
    acct_id = acct['id']
    if (acct.get('status') or '') == 'reauth_needed':
        log(f"  [LA]계정#{acct_id} 재연결 필요 상태 — 자동 재시도 생략")
        return
    first_poll = not acct['refresh_token_enc']   # 최초 연결(아직 쿠키 없음)

    cookies = None
    if acct['refresh_token_enc']:
        try:
            cookies = chat_la.refresh_cookies(_json.loads(crypto_util.decrypt(acct['refresh_token_enc'])))
        except Exception as e:
            log(f"  [LA]계정#{acct_id} 쿠키 갱신 실패({repr(e)[:50]}) — 저장 비번으로 재로그인 시도")
    if cookies is None:
        if not acct['password_enc']:
            _mark_status(conn, acct_id, 'reauth_needed', '저장된 비밀번호 없음 — 재연결 필요')
            return
        if not chat_la.playwright_available():
            log(f"  [LA]계정#{acct_id} 로그인 필요하지만 이 환경엔 Playwright 없음 — 스킵")
            return
        try:
            cookies = chat_la.login_and_get_cookies(
                acct['samsam_email'], crypto_util.decrypt(acct['password_enc']))
        except Exception as e:
            log(f"  [LA]계정#{acct_id} 로그인 실패: {repr(e)[:100]}")
            _mark_status(conn, acct_id, 'reauth_needed', repr(e)[:200])
            return

    try:
        channels = chat_la.list_channels(cookies)
    except Exception as e:
        log(f"  [LA]계정#{acct_id} 채널 조회 실패: {repr(e)[:100]}")
        _mark_status(conn, acct_id, 'error', repr(e)[:200])
        return

    for ch in channels:
        if not ch.get('channel_url'):
            continue
        room = {'room_name': ch['room_name'], 'host_or_guest': 'host',
                'member': ch['counterpart_member'], 'contract_status': None,
                'chat_room_status': None, 'start_date': None, 'end_date': None,
                'last_message': ch['last_message'], 'last_message_time': ch['last_message_time']}
        st = conn.execute(
            "SELECT last_notified_time FROM samsam_chat_rooms WHERE account_id=%s AND samsam_room_key=%s",
            (acct_id, ch['channel_url'])).fetchone()
        prev_notif = (st['last_notified_time'] if st else None) or 0
        room_id = _upsert_room(conn, acct_id, ch['channel_url'], room, ch['counterpart_nickname'])
        lmt = ch['last_message_time'] or 0
        # 대화 내용 적재 — 리브애니웨어는 이력 조회 API가 없어서(게이트웨이 404/500) 폴링 때
        # 보이는 '마지막 메시지'만 모은다. 그래서 연결 이후 오간 대화가 쌓이는 구조다.
        # 보낸 사람 정보가 payload에 없어, 안읽음이 있으면 상대방 발신으로 본다(그 외는 미상).
        if ch.get('last_message') and ch.get('last_message_id'):
            sender = ch['counterpart_member'] if (ch.get('unread') or 0) > 0 else ''
            try:
                conn.execute(
                    "INSERT INTO samsam_chat_messages"
                    " (room_id, msg_key, sender, receiver, message, message_type, message_time)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (room_id, msg_key) DO NOTHING",
                    (room_id, f"la:{ch['last_message_id']}", sender, '',
                     ch['last_message'], 'text', lmt))
                conn.commit()
            except Exception as e:
                log(f"    [LA]메시지 적재 실패(room {room_id}): {repr(e)[:80]}")
        # 신규 수신 = 최초폴 아님 + 안읽음 있음 + 마지막메시지 시각이 직전 알림보다 최신. (fail-safe)
        if (not first_poll) and ch.get('unread', 0) > 0 and lmt > prev_notif:
            label = acct.get('label') or acct.get('samsam_email') or '리브애니웨어'
            preview = (ch.get('last_message') or '').strip().replace('\n', ' ')[:60]
            text = (f"[rendit] 리브애니웨어 새 문의\n계정: {label}\n"
                    f"{ch.get('counterpart_nickname') or '게스트'}: {preview or '(내용 없음)'}")
            try:
                if kakao_notify.send_to_member(conn, acct['member_id'], text, CHAT_URL, "채팅 열기"):
                    log(f"    [LA]카톡 알림 발송(room {room_id}, member#{acct['member_id']})")
            except Exception as e:
                log(f"    [LA]카톡 알림 오류: {repr(e)[:100]}")
        if lmt > prev_notif:
            conn.execute("UPDATE samsam_chat_rooms SET last_notified_time=%s WHERE id=%s", (lmt, room_id))
            conn.commit()

    conn.execute(
        "UPDATE samsam_accounts SET refresh_token_enc=%s, status='ok', last_error=NULL, "
        "last_polled_at=%s WHERE id=%s",
        (crypto_util.encrypt(_json.dumps(cookies)), _now(), acct_id))
    conn.commit()
    log(f"  [LA]계정#{acct_id}({acct['label'] or acct['samsam_email']}) 채널 {len(channels)}개 갱신")


def poll_one(conn, acct):
    """공급자별 폴러 디스패처."""
    if (acct.get('provider') or 'samsam') == 'liveanywhere':
        return poll_liveanywhere_account(conn, acct)
    return poll_account(conn, acct)


def poll_all(conn):
    """연결된(비활성 아닌) 전체 계정을 공급자별로 폴링. 서버 크론·GH Actions가 공용으로 씀."""
    accounts = conn.execute(
        "SELECT id, member_id, provider, samsam_email, label, password_enc, refresh_token_enc, "
        "samsam_member_id, status FROM samsam_accounts WHERE status != 'disabled'").fetchall()
    for acct in accounts:
        poll_one(conn, dict(acct))
    return len(accounts)


def main():
    db.init_db()
    conn = db.connect()
    n = poll_all(conn)
    n_sent = process_outbox(conn)
    conn.close()
    log(f"완료 — 연결된 삼삼 계정 {n}개 폴링, 답장 {n_sent}건 처리")


if __name__ == '__main__':
    main()
