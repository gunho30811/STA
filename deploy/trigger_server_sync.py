# -*- coding: utf-8 -*-
"""로컬 크롤 완료 직후 서버의 Supabase→로컬pg 동기화를 원격 실행.

.env의 ORACLE_HOST/USER/PASSWORD로 SSH 접속(paramiko — 윈도우라 sshpass 없음).
실패해도 치명적이지 않음: 서버 크론이 6시간마다 어차피 동기화한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

try:
    import paramiko
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(os.environ["ORACLE_HOST"], username=os.environ["ORACLE_USER"],
              password=os.environ["ORACLE_PASSWORD"], timeout=20)
    _, o, e = c.exec_command("/home/ubuntu/STA/deploy/sync_from_supabase.sh && echo SYNC_OK",
                             timeout=1800)
    out = o.read().decode()
    print(out[-200:] if out else "(no output)")
    c.close()
    print("서버 동기화 트리거 완료" if "SYNC_OK" in out else "서버 동기화 결과 불확실 — 크론이 보완")
except Exception as exc:  # 네트워크/서버 문제 — 크론 동기화가 있으니 경고만
    print(f"서버 동기화 트리거 실패(크론이 6시간마다 보완): {repr(exc)[:150]}")
