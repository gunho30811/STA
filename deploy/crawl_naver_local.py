# -*- coding: utf-8 -*-
"""네이버 부동산 로컬 크롤 → Supabase 적재 → 서버 sync(Supabase→오라클) 트리거.

네이버(new.land.naver.com)는 삼삼처럼 **데이터센터 IP를 차단**(ERR_CONNECTION_RESET)하므로
서버 크론이 아니라 가정용 IP인 이 PC에서 돌린다.

DB 쓰기 경로: 삼삼처럼 오라클에 SSH 터널로 직적재하려 했으나, 2시간짜리 네이버 크롤(동별
연결 수천 회)에서 터널 write가 불안정해 데이터가 유실됐다. 그래서 네이버는 **listings 원본을
Supabase에 적재**(검증된 경로 — 07-15 데이터도 이 경로)하고, 끝나면 서버 sync를 트리거해
Supabase→오라클로 반영한다. net_profit/insights 재계산은 삼삼 일일 크롤(05:00)이 담당.

기본: 예약률 30%+ 동네(deploy/naver_target_dongs.txt), 월세, 주거 6종.
윈도우 작업 스케줄러(rendit-naver-daily)가 매일 02:00 실행. PC가 켜져 있어야 함.
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

DONGS_FILE = os.path.join(ROOT, "deploy", "naver_target_dongs.txt")
TYPES = os.environ.get("NAVER_LOCAL_TYPES", "APT,OPST,VL,OR,DDDGG,SG")


def main():
    supa = os.environ.get("SUPABASE_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not supa:
        print("[FATAL] SUPABASE_DATABASE_URL 없음", flush=True)
        sys.exit(1)
    env = dict(os.environ)
    env["DATABASE_URL"] = supa               # Supabase에 적재(검증된 경로)
    env["PYTHONPATH"] = ROOT + os.pathsep + os.path.join(ROOT, "pipeline", "naver")
    py = sys.executable

    # crawl_state 리셋 — done 표시된 동을 건너뛰지 않게(재크롤 강제)
    r = subprocess.run(
        [py, "-c", "import db; c=db.connect(); c.execute('DELETE FROM crawl_state'); "
         "c.commit(); print('crawl_state 리셋 완료')"], cwd=ROOT, env=env)
    if r.returncode != 0:
        print("[warn] crawl_state 리셋 실패 — 진행", flush=True)

    steps = [
        [py, "pipeline/naver/crawler.py", "--sidos", "서울시,경기도,인천시",
         "--types", TYPES, "--dongs-file", DONGS_FILE],
        [py, "pipeline/naver/create_live_view.py"],       # Supabase 뷰(백업)
        [py, "deploy/trigger_server_sync.py"],            # Supabase→오라클 반영
    ]
    rc = 0
    for step in steps:
        print(f"\n[run] {' '.join(step[1:])}", flush=True)
        p = subprocess.run(step, cwd=ROOT, env=env)
        if p.returncode != 0:
            print(f"[warn] rc={p.returncode}: {step[1]}", flush=True)
            rc = p.returncode
            if step[1].endswith("crawler.py"):
                break
    print(f"\n[done] rc={rc}", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
