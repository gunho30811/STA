# -*- coding: utf-8 -*-
"""네이버 부동산 로컬 크롤 → 오라클 서버 DB에 직접 적재 (SSH 터널).

네이버(new.land.naver.com)도 삼삼처럼 **데이터센터 IP를 차단**(ERR_CONNECTION_RESET)하므로
서버 크론이 아니라 가정용 IP인 이 PC에서 돌려야 한다. crawl_samsam_local.py와 동일한 SSH
포워딩 터널로 오라클 pg에 직접 쓴다.

기본: 예약률 30%+ 동네(deploy/naver_target_dongs.txt)만, 월세, 전 주거유형.
윈도우 작업 스케줄러(rendit-naver-daily)가 매일 실행. PC가 켜져 있어야 함.
"""
import os
import select
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

LOCAL_PORT = 15433
DONGS_FILE = os.path.join(ROOT, "deploy", "naver_target_dongs.txt")
TYPES = os.environ.get("NAVER_LOCAL_TYPES", "APT,OPST,VL,OR,DDDGG,SG")   # 주거 6종 월세


def _ssh():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(os.environ["ORACLE_HOST"], username=os.environ["ORACLE_USER"],
              password=os.environ["ORACLE_PASSWORD"], timeout=20)
    return c


def _run(ssh, cmd):
    _, o, e = ssh.exec_command(cmd, timeout=30)
    return o.read().decode().strip(), e.read().decode().strip()


def _make_handler(transport, host, port):
    class H(BaseRequestHandler):
        def handle(self):
            try:
                chan = transport.open_channel("direct-tcpip", (host, port),
                                              self.request.getpeername())
            except Exception as ex:
                print(f"[tunnel] 채널 실패: {ex}", flush=True)
                return
            if chan is None:
                return
            try:
                while True:
                    r, _, _ = select.select([self.request, chan], [], [])
                    if self.request in r:
                        d = self.request.recv(4096)
                        if not d:
                            break
                        chan.sendall(d)
                    if chan in r:
                        d = chan.recv(4096)
                        if not d:
                            break
                        self.request.sendall(d)
            finally:
                chan.close()
                self.request.close()
    return H


def main():
    ssh = _ssh()
    pg_ip, _ = _run(ssh, "sudo docker inspect pg "
                    "--format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'")
    pw, _ = _run(ssh, "grep '^LOCAL_PG_PASSWORD=' /home/ubuntu/STA/.env | cut -d= -f2-")
    if not pg_ip or not pw:
        print(f"[FATAL] pg_ip={pg_ip!r} pw_len={len(pw)}", flush=True)
        sys.exit(1)
    print(f"[tunnel] pg {pg_ip}:5432 → 127.0.0.1:{LOCAL_PORT}", flush=True)

    server = ThreadingTCPServer(("127.0.0.1", LOCAL_PORT),
                                _make_handler(ssh.get_transport(), pg_ip, 5432))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

    env = dict(os.environ)
    env["DATABASE_URL"] = ("postgresql://postgres:%s@127.0.0.1:%d/rendit?sslmode=disable"
                           % (urllib.parse.quote(pw, safe=""), LOCAL_PORT))
    env["PYTHONPATH"] = ROOT + os.pathsep + os.path.join(ROOT, "pipeline", "naver")
    py = sys.executable

    steps = [
        [py, "pipeline/naver/crawler.py", "--sidos", "서울시,경기도,인천시",
         "--types", TYPES, "--dongs-file", DONGS_FILE],
        [py, "pipeline/naver/create_live_view.py"],
        [py, "pipeline/integrate/build_integrated.py"],
        [py, "pipeline/refresh_insights.py"],
    ]
    rc = 0
    try:
        for step in steps:
            print(f"\n[run] {' '.join(step[1:])}", flush=True)
            p = subprocess.run(step, cwd=ROOT, env=env)
            if p.returncode != 0:
                print(f"[warn] rc={p.returncode}: {step[1]}", flush=True)
                rc = p.returncode
                if step[1].endswith("crawler.py"):
                    break
    finally:
        server.shutdown()
        ssh.close()
    print(f"\n[done] rc={rc}", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
