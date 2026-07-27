# -*- coding: utf-8 -*-
"""카카오톡 '나에게 보내기'(memo/default/send)로 회원에게 알림 전송.

members 테이블에 저장된 회원의 카카오 refreshToken(암호화)으로 accessToken을 갱신해
본인 카톡으로 메시지를 보낸다. 로그인 앱(KAKAO_CLIENT_ID/SECRET) 기준.
Flask 의존 없음 — GH Actions/크론/로컬 어디서든 DB만 있으면 동작(채팅 폴러가 호출).

사전 준비:
  - 카카오 콘솔(로그인 앱) > 카카오 로그인 > 동의항목에서 '카카오톡 메시지 전송(talk_message)' 활성화.
  - 회원이 /auth/kakao?notify=1 로 로그인해 talk_message 동의 → refreshToken 저장 + kakao_notify=TRUE.
"""
import json
import os

import requests

import crypto_util  # common/ 은 chat_poll이 sys.path에 추가

TOKEN_URL = "https://kauth.kakao.com/oauth/token"
MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"


def _refresh_access_token(refresh_token):
    """refreshToken으로 accessToken 갱신. (access, new_refresh_or_None) 반환.

    카카오는 refreshToken 잔여기간이 1개월 미만일 때만 새 refreshToken을 함께 준다."""
    data = {"grant_type": "refresh_token",
            "client_id": os.environ.get("KAKAO_CLIENT_ID", ""),
            "refresh_token": refresh_token}
    if os.environ.get("KAKAO_CLIENT_SECRET"):
        data["client_secret"] = os.environ["KAKAO_CLIENT_SECRET"]
    r = requests.post(TOKEN_URL, data=data, timeout=10)
    r.raise_for_status()
    j = r.json()
    return j.get("access_token"), j.get("refresh_token")


def _send_memo(access_token, text, link_url, button_title="열어보기"):
    obj = {"object_type": "text", "text": text[:1000],
           "link": {"web_url": link_url, "mobile_web_url": link_url},
           "button_title": button_title}
    r = requests.post(
        MEMO_URL, headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(obj, ensure_ascii=False)}, timeout=10)
    return r.status_code, r.text[:200]


def send_to_member(conn, member_id, text, link_url, button_title="열어보기"):
    """해당 회원이 카톡 알림을 켜뒀으면 본인 카톡으로 발송. 실패는 로그만(호출부에 영향 없음).
    True=발송함 / False=대상 아님·실패."""
    try:
        row = conn.execute(
            "SELECT kakao_refresh_token_enc FROM members "
            "WHERE id=%s AND kakao_notify=TRUE AND kakao_refresh_token_enc IS NOT NULL",
            (member_id,)).fetchone()
    except Exception as e:
        print(f"[kakao_notify] 조회 오류: {repr(e)[:120]}", flush=True)
        return False
    if not row:
        return False
    try:
        rt = crypto_util.decrypt(row["kakao_refresh_token_enc"])
        access, new_rt = _refresh_access_token(rt)
        if new_rt and new_rt != rt:
            conn.execute("UPDATE members SET kakao_refresh_token_enc=%s WHERE id=%s",
                         (crypto_util.encrypt(new_rt), member_id))
            conn.commit()
        if not access:
            print(f"[kakao_notify] member#{member_id} accessToken 없음", flush=True)
            return False
        sc, body = _send_memo(access, text, link_url, button_title)
        if sc == 200:
            return True
        print(f"[kakao_notify] member#{member_id} 발송 실패 {sc}: {body}", flush=True)
        return False
    except Exception as e:
        print(f"[kakao_notify] member#{member_id} 오류: {repr(e)[:150]}", flush=True)
        return False
