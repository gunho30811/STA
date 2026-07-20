# -*- coding: utf-8 -*-
"""삼삼 로컬 크롤 → 오라클 서버 DB에 직접 적재 (Supabase 안 거침).

동작:
  1) 오라클에 SSH 접속해 pg 컨테이너 IP와 비밀번호(LOCAL_PG_PASSWORD)를 읽는다.
  2) SSH 로컬 포워딩 터널을 연다: 127.0.0.1:15432 → (pg 컨테이너):5432.
     - pg 포트는 호스트에 노출돼 있지 않지만, 포워딩 목적지는 SSH 서버(오라클) 쪽에서
       해석되므로 도커 브리지 IP로 바로 연결된다. 서버 방화벽/포트 설정을 건드릴 필요 없음.
  3) DATABASE_URL을 그 터널로 지정하고 크롤·후처리 파이프라인을 순서대로 실행한다.
     - 스케줄(예약) 조회는 이 PC(가정용 IP)라 삼삼 소프트차단을 피한다.
     - DB 쓰기는 전부 오라클 pg로 간다.
기본은 수도권 오피스텔 전량 매일 갱신(쿨다운 해제). --types 로 대상 조정.

윈도우 작업 스케줄러(rendit-samsam-daily)가 매일 05:00 실행. PC가 켜져 있어야 함.
"""
import os
import select
import socket
import subprocess
import sys
import threading
import urllib.parse
from socketserver import BaseRequestHandler, ThreadingTCPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paramiko  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

LOCAL_PORT = 15432
TYPES = os.environ.get("SAMSAM_LOCAL_TYPES", "OFFICETEL")   # 기본 오피스텔


def _ssh():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(os.environ["ORACLE_HOST"], username=os.environ["ORACLE_USER"],
              password=os.environ["ORACLE_PASSWORD"], timeout=20)
    return c


def _run(ssh, cmd):
    _, o, e = ssh.exec_command(cmd, timeout=30)
    return o.read().decode().strip(), e.read().decode().strip()


def _make_handler(transport, remote_host, remote_port):
    class H(BaseRequestHandler):
        def handle(self):
            try:
                chan = transport.open_channel(
                    "direct-tcpip", (remote_host, remote_port),
                    self.request.getpeername())
            except Exception as ex:
                print(f"[tunnel] 채널 열기 실패: {ex}", flush=True)
                return
            if chan is None:
                return
            try:
                while True:
                    r, _, _ = select.select([self.request, chan], [], [])
                    if self.request in r:
                        data = self.request.recv(4096)
                        if not data:
                            break
                        chan.sendall(data)
                    if chan in r:
                        data = chan.recv(4096)
                        if not data:
                            break
                        self.request.sendall(data)
            finally:
                chan.close()
                self.request.close()
    return H


def main():
    ssh = _ssh()
    # pg 컨테이너 IP + 비밀번호 조회
    pg_ip, _ = _run(ssh, "sudo docker inspect pg "
                    "--format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'")
    pw, _ = _run(ssh, "grep '^LOCAL_PG_PASSWORD=' /home/ubuntu/STA/.env | cut -d= -f2-")
    if not pg_ip or not pw:
        print(f"[FATAL] pg_ip={pg_ip!r} pw_len={len(pw)} — 조회 실패", flush=True)
        sys.exit(1)
    print(f"[tunnel] pg 컨테이너 {pg_ip}:5432 → 127.0.0.1:{LOCAL_PORT}", flush=True)

    transport = ssh.get_transport()
    server = ThreadingTCPServer(("127.0.0.1", LOCAL_PORT),
                                _make_handler(transport, pg_ip, 5432))
    server.daemon_threads = True
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    db_url = ("postgresql://postgres:%s@127.0.0.1:%d/rendit?sslmode=disable"
              % (urllib.parse.quote(pw, safe=""), LOCAL_PORT))
    env = dict(os.environ)
    env["DATABASE_URL"] = db_url
    env["SAMSAM_BOOKED_COOLDOWN_DAYS"] = "0"    # 매일 전량 갱신(쿨다운 해제)
    # 계정당 하루 예약조회 한도(~5,000건 실측) 아래로 분배 — 안 넣으면 1계정이 전량 시도→차단.
    # 오피스텔(6.7k)은 계정 2개면 계정당 ~3.4k. 유형/매물 늘리면 이 값과 계정 수를 함께 키운다.
    env.setdefault("SAMSAM_REFRESH_DAILY_LIMIT", "4000")
    env["PYTHONPATH"] = ROOT + os.pathsep + os.path.join(ROOT, "pipeline", "samsam")

    py = sys.executable
    steps = [
        [py, "pipeline/samsam/crawler.py", "--types", TYPES],
        [py, "pipeline/samsam/snapshot.py"],
        [py, "pipeline/integrate/build_integrated.py"],
        [py, "pipeline/refresh_insights.py"],
    ]
    rc = 0
    try:
        for step in steps:
            print(f"\n[run] {' '.join(step)}", flush=True)
            p = subprocess.run(step, cwd=ROOT, env=env)
            if p.returncode != 0:
                print(f"[warn] 종료코드 {p.returncode}: {step[1]}", flush=True)
                rc = p.returncode
                if step[1].endswith("crawler.py"):
                    break   # 크롤 실패면 후처리 무의미
    finally:
        server.shutdown()
        ssh.close()
    print(f"\n[done] rc={rc}", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
