# -*- coding: utf-8 -*-
"""삼삼엠투 연결 계정 비번·토큰 암호화. 키: .env CHAT_ENC_KEY (Fernet, urlsafe base64 32바이트)."""
import os
from functools import lru_cache

from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()


@lru_cache(maxsize=1)
def _fernet():
    key = os.environ.get("CHAT_ENC_KEY")
    if not key:
        raise RuntimeError("CHAT_ENC_KEY 환경변수를 설정하세요 (삼삼 채팅 계정 암호화 키).")
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
