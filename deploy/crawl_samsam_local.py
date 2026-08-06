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
기본은 수도권 오피스텔 전량 매일 갱신(쿨다운 해제) + 추가 지역(부산·천안) 전 유형 갱신.
--types / SAMSAM_LOCAL_TYPES 로 수도권 대상 유형을, SAMSAM_EXTRA_REGIONS로 추가 지역을 조정한다.

윈도우 작업 스케줄러(rendit-samsam-daily)가 매일 05:00 실행. PC가 켜져 있어야 함.
"""
import os
import select
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from socketserver import BaseRequestHandler, ThreadingTCPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paramiko  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

LOCAL_PORT = 15432
TYPES = os.environ.get("SAMSAM_LOCAL_TYPES", "OFFICETEL")   # 기본 오피스텔(수도권 물량이 커서)

# 수도권 외 추가 지역은 매물이 적어(부산 1.3k·천안 0.15k) 전 유형을 매일 돌린다.
# 토큰은 common/target_regions.py의 EXTRA_REGIONS와 같은 표기(시군구 필터로도 그대로 씀).
EXTRA_REGIONS = [s.strip() for s in
                 os.environ.get("SAMSAM_EXTRA_REGIONS", "부산,천안시").split(",") if s.strip()]
# 계정당 하루 예약조회 한도(~5,000 실측) 안에서 수도권 오피스텔(계정당 ~3.4k)과 나눠 쓴다.
EXTRA_LIMIT = os.environ.get("SAMSAM_EXTRA_DAILY_LIMIT", "800")
ONLY_EXTRA = os.environ.get("SAMSAM_ONLY_EXTRA") == "1"   # 추가 지역만(지역 편입 1회성 실행용)


def _ssh():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(os.environ["ORACLE_HOST"], username=os.environ["ORACLE_USER"],
              password=os.environ["ORACLE_PASSWORD"], timeout=20)
    # 수 시간짜리 크롤 동안 유휴 SSH가 끊기지 않게 keepalive (NAT/방화벽 타임아웃 대비).
    c.get_transport().set_keepalive(15)
    return c


def _run(ssh, cmd):
    _, o, e = ssh.exec_command(cmd, timeout=30)
    return o.read().decode().strip(), e.read().decode().strip()


def _make_handler(transport, remote_host, remote_port):
    class H(BaseRequestHandler):
        def handle(self):
            # 연결 폭주/일시 장애로 채널 열기가 실패할 수 있어 3회 재시도
            # (여기서 조용히 죽으면 크롤 쪽 DB 쓰기가 유실됨).
            chan = None
            for attempt in range(3):
                try:
                    chan = transport.open_channel(
                        "direct-tcpip", (remote_host, remote_port),
                        self.request.getpeername())
                    if chan is not None:
                        break
                except Exception as ex:
                    print(f"[tunnel] 채널 열기 실패({attempt + 1}/3): {ex}", flush=True)
                time.sleep(0.5 * (attempt + 1))
            if chan is None:
                print("[tunnel] 채널 포기 — DB 연결 1건 실패", flush=True)
                self.request.close()
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
    # (명령, env 덮어쓰기, 실패 시 중단)
    steps = []
    if not ONLY_EXTRA:
        # 수도권 오피스텔 — 추가 지역은 아래 전용 스텝이 맡으므로 여기선 제외(중복 조회 방지)
        steps.append(([py, "pipeline/samsam/crawler.py", "--types", TYPES],
                      {"RENDIT_EXTRA_REGIONS": ""}, True))
    for token in EXTRA_REGIONS:
        steps.append(([py, "pipeline/samsam/crawler.py", "--sigungu", token],
                      {"RENDIT_EXTRA_REGIONS": token,
                       "SAMSAM_REFRESH_DAILY_LIMIT": EXTRA_LIMIT}, False))
    steps += [
        ([py, "pipeline/samsam/snapshot.py"], {}, False),
        # 최신 예약률로 네이버 크롤 대상 동(예약률 30%+) 재생성 → 다음 네이버 02:00 실행이 사용
        ([py, "pipeline/samsam/gen_naver_dongs.py"], {}, False),
        ([py, "pipeline/integrate/build_integrated.py"], {}, False),
        ([py, "pipeline/refresh_insights.py"], {}, False),
    ]
    rc = 0
    try:
        for step, over, critical in steps:
            print(f"\n[run] {' '.join(step)}"
                  + (f"  [{' '.join(f'{k}={v}' for k, v in over.items())}]" if over else ""),
                  flush=True)
            p = subprocess.run(step, cwd=ROOT, env={**env, **over})
            if p.returncode != 0:
                print(f"[warn] 종료코드 {p.returncode}: {step[1]}", flush=True)
                rc = p.returncode
                if critical:
                    break   # 수도권 크롤이 실패하면 후처리 무의미
    finally:
        server.shutdown()
        ssh.close()
    print(f"\n[done] rc={rc}", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
