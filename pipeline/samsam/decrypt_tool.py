# -*- coding: utf-8 -*-
"""CHAT_ENC_KEY로 암호화된 값 수동 복호화 (디버깅/운영용).

samsam_accounts.password_enc / refresh_token_enc 같은 암호문을 DB에서 직접 복사해
로컬에서 무슨 값인지 확인할 때 씀. 폴링 로직(chat_poll.py)과 별개로 두어, 계정 상태가
'reauth_needed'로 막혔을 때 저장된 비번이 맞는지 등을 빠르게 점검할 수 있게 한다.

사용법:
  python pipeline/samsam/decrypt_tool.py <암호문>

필요 환경변수(.env): CHAT_ENC_KEY
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv

load_dotenv(os.path.join(BASE_DIR, '.env'))

import crypto_util


def main():
    if len(sys.argv) != 2:
        print("사용법: python pipeline/samsam/decrypt_tool.py <암호문>")
        sys.exit(1)
    print(crypto_util.decrypt(sys.argv[1]))


if __name__ == '__main__':
    main()
