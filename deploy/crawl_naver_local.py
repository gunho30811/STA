# -*- coding: utf-8 -*-
"""네이버 부동산 로컬 크롤 → 오라클 서버 pg에 SSH 터널로 직접 적재.

네이버(new.land.naver.com)는 삼삼처럼 **데이터센터 IP를 차단**(ERR_CONNECTION_RESET)하므로
서버 크론이 아니라 가정용 IP인 이 PC에서 돌린다.

한때 터널 write 유실(07-23, 93k 중 15건만 저장) 때문에 Supabase를 중간 적재소로 썼으나,
Supabase 프로젝트가 정지되어(07-30) 삼삼과 같은 SSH 터널 직적재로 전환했다. 유실 재발 대비:
  - 터널에 keepalive + 채널 열기 재시도 (crawl_samsam_local 공용 코드)
  - 크롤 후 실제 적재 건수를 crawl_state 집계와 비교 검증 — 80% 미만이면 실패(rc=2)

기본: 예약률 30%+ 동네(deploy/naver_target_dongs.txt), 월세, 주거 6종.
수도권 다음에 추가 지역(부산·천안)을 시도별로 이어서 돈다(naver_target_dongs_extra.txt).
NAVER_ONLY_EXTRA=1 이면 추가 지역만 크롤(지역 신규 편입 때 1회성 실행용).
윈도우 작업 스케줄러(rendit-naver-daily)가 매일 02:00 실행. PC가 켜져 있어야 함.
삼삼(15432)과 다른 로컬 포트(15433)를 써서 두 크롤이 겹쳐 돌아도 충돌 없음.
"""
import datetime
import os
import subprocess
import sys
import threading
import urllib.parse
from socketserver import ThreadingTCPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv  # noqa: E402

from crawl_samsam_local import _make_handler, _run, _ssh  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

LOCAL_PORT = 15433   # 삼삼 크롤(15432)과 동시 실행 대비 별도 포트
DONGS_FILE = os.path.join(ROOT, "deploy", "naver_target_dongs.txt")
DONGS_EXTRA = os.path.join(ROOT, "deploy", "naver_target_dongs_extra.txt")
TYPES = os.environ.get("NAVER_LOCAL_TYPES", "APT,OPST,VL,OR,DDDGG,SG")

# 수도권 외 추가 지역 스텝 — '네이버 시도명:시군구키워드' 콤마구분(시군구 비우면 시도 전체).
# 시도별로 나눠 도는 이유: 동 화이트리스트가 이름만 담고 있어 한 번에 돌리면 동명이인이
# 대상 밖 지역을 끌고 온다. 대상 지역 자체는 common/target_regions.py가 기준.
EXTRA_STEPS = [s.strip() for s in
               os.environ.get("NAVER_EXTRA_REGIONS", "부산시:,충청남도:천안").split(",") if s.strip()]
ONLY_EXTRA = os.environ.get("NAVER_ONLY_EXTRA") == "1"   # 추가 지역만 크롤(수도권 스킵)

# 크롤 후 적재 검증 — CRAWL_START 이후 저장된 listings 수를 crawl_state 집계와 비교.
# (같은 매물이 여러 동에 걸리면 집계가 약간 부풀므로 80%를 하한으로 잡는다.
#  예전 유실 사고는 0.02% 수준이라 이 검증으로 충분히 잡힌다.)
_VERIFY_CODE = """
import os, sys
import db
c = db.connect()
n = c.execute("SELECT count(*) FROM listings WHERE crawled_at >= %s",
              (os.environ["CRAWL_START"],)).fetchone()[0]
exp = c.execute("SELECT coalesce(sum(n_articles), 0) FROM crawl_state "
                "WHERE status='done'").fetchone()[0]
print(f"[verify] 적재 {n}건 / crawl_state 기대 {exp}건", flush=True)
c.close()
sys.exit(0 if exp == 0 or n >= exp * 0.8 else 2)
"""


def _extra_steps(py):
    """추가 지역(부산·천안) 크롤 스텝 목록.

    대상 동 파일(naver_target_dongs_extra.txt)이 있으면 그 화이트리스트로 좁히고,
    아직 없으면(최초 부트스트랩) 해당 지역 전체 동을 돈다. 파일이 있는데 비어 있으면
    = 예약률 30%+ 동이 없다는 뜻이라 건너뛴다."""
    if not EXTRA_STEPS:
        return []
    n_dongs = None
    if os.path.exists(DONGS_EXTRA):
        with open(DONGS_EXTRA, encoding="utf-8") as f:
            n_dongs = sum(1 for ln in f if ln.strip())
        if n_dongs == 0:
            print("[extra] 대상 동 0개 — 추가 지역 크롤 생략", flush=True)
            return []
    out = []
    for spec in EXTRA_STEPS:
        sido, _, gu = spec.partition(":")
        cmd = [py, "pipeline/naver/crawler.py", "--sidos", sido.strip(), "--types", TYPES]
        if gu.strip():
            cmd += ["--gu", gu.strip()]
        if n_dongs:
            cmd += ["--dongs-file", DONGS_EXTRA]
        out.append(cmd)
        scope = f"대상 동 {n_dongs}개" if n_dongs else "전체 동(부트스트랩)"
        print(f"[extra] {sido.strip()}{' ' + gu.strip() if gu.strip() else ''} — {scope}", flush=True)
    return out


def main():
    ssh = _ssh()
    pg_ip, _ = _run(ssh, "sudo docker inspect pg "
                    "--format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'")
    pw, _ = _run(ssh, "grep '^LOCAL_PG_PASSWORD=' /home/ubuntu/STA/.env | cut -d= -f2-")
    if not pg_ip or not pw:
        print(f"[FATAL] pg_ip={pg_ip!r} pw_len={len(pw)} — 조회 실패", flush=True)
        sys.exit(1)
    print(f"[tunnel] pg 컨테이너 {pg_ip}:5432 → 127.0.0.1:{LOCAL_PORT}", flush=True)

    server = ThreadingTCPServer(("127.0.0.1", LOCAL_PORT),
                                _make_handler(ssh.get_transport(), pg_ip, 5432))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

    env = dict(os.environ)
    env["DATABASE_URL"] = ("postgresql://postgres:%s@127.0.0.1:%d/rendit?sslmode=disable"
                           % (urllib.parse.quote(pw, safe=""), LOCAL_PORT))
    env["PYTHONPATH"] = ROOT + os.pathsep + os.path.join(ROOT, "pipeline", "naver")
    env["CRAWL_START"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    py = sys.executable

    rc = 0
    try:
        # crawl_state 리셋 — done 표시된 동을 건너뛰지 않게(재크롤 강제)
        r = subprocess.run(
            [py, "-c", "import db; c=db.connect(); c.execute('DELETE FROM crawl_state'); "
             "c.commit(); print('crawl_state 리셋 완료')"], cwd=ROOT, env=env)
        if r.returncode != 0:
            print("[warn] crawl_state 리셋 실패 — 진행", flush=True)

        # (라벨, 명령, 실패 시 중단) — 수도권 크롤이 깨지면 뒤가 무의미해 중단하지만,
        # 추가 지역 한 곳이 실패해도 나머지 지역·뷰 갱신은 계속한다.
        steps = []
        if not ONLY_EXTRA:
            steps.append(("crawler.py 수도권",
                          [py, "pipeline/naver/crawler.py", "--sidos", "서울시,경기도,인천시",
                           "--types", TYPES, "--dongs-file", DONGS_FILE], True))
        steps += [(f"crawler.py {c[3]}{' ' + c[c.index('--gu') + 1] if '--gu' in c else ''}",
                   c, False) for c in _extra_steps(py)]
        steps += [
            ("verify", [py, "-c", _VERIFY_CODE], False),          # 적재 건수 검증(유실 감지)
            ("create_live_view.py",
             [py, "pipeline/naver/create_live_view.py"], False),  # nl_live 뷰 갱신(오라클)
        ]
        for label, step, critical in steps:
            print(f"\n[run] {label}", flush=True)
            p = subprocess.run(step, cwd=ROOT, env=env)
            if p.returncode != 0:
                print(f"[warn] rc={p.returncode}: {label}", flush=True)
                rc = p.returncode
                if critical:
                    break
    finally:
        server.shutdown()
        ssh.close()
    print(f"\n[done] rc={rc}", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
