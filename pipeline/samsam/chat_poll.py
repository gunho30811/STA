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

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

load_dotenv(os.path.join(BASE_DIR, '.env'))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import chat_auth
import crypto_util
import db

MSG_LIMIT = 50  # 폴링마다 방당 최근 메시지 N개만 조회 — 신규분만 있으면 충분, 전체 이력 아님


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


def poll_account(conn, acct):
    acct_id = acct['id']
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
    for room_key, room in host_rooms.items():
        nickname = None
        counterpart = room.get('member')
        if counterpart:
            nickname = _get_nickname(id_token, counterpart, nickname_cache)
        room_id = _upsert_room(conn, acct_id, room_key, room, nickname)
        _poll_messages(conn, room_id, room_key, id_token)

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


def process_outbox(conn):
    """대기 중인 답장(samsam_chat_outbox status='pending')을 실제 발송.

    로그인과 마찬가지로 브라우저 자동화(Playwright)가 필요해 GH Actions에서만 처리한다
    (Vercel 1분 cron은 poll_all만 돌리고 여긴 안 건드림 — chat_api_cron_poll이 이 함수를
    호출하지 않는 이유).
    """
    if not chat_auth.playwright_available():
        return 0
    items = conn.execute(
        """SELECT o.id, o.room_id, o.message, r.room_name, a.samsam_email, a.password_enc
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
        try:
            password = crypto_util.decrypt(item['password_enc'])
            chat_auth.send_message(item['samsam_email'], password, item['room_name'], item['message'])
            _mark_outbox(conn, item['id'], 'sent')
            log(f"  outbox#{item['id']} 방({item['room_name']}) 발송 완료")
        except Exception as e:
            log(f"  outbox#{item['id']} 발송 실패: {repr(e)[:120]}")
            _mark_outbox(conn, item['id'], 'failed', repr(e)[:200])
    return len(items)


def poll_all(conn):
    """연결된(비활성 아닌) 전체 계정을 폴링. GH Actions(main)와 Vercel cron 엔드포인트가 공용으로 씀."""
    accounts = conn.execute(
        "SELECT id, member_id, samsam_email, label, password_enc, refresh_token_enc, "
        "samsam_member_id FROM samsam_accounts WHERE status != 'disabled'").fetchall()
    for acct in accounts:
        poll_account(conn, dict(acct))
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
