"""서버 ~/STA/.env의 DATABASE_URL을 로컬 pg 컨테이너로 전환.

- CRLF 혼재 → LF 정규화, 줄 중간에 붙어버린 LOCAL_PG_PASSWORD= 수리 포함.
- 기존 Supabase URL은 SUPABASE_DATABASE_URL 키로 보존(동기화 스크립트가 사용).
- 실행 전 .env.bak-cutover 백업. 멱등 — 이미 전환된 상태면 재실행해도 동일 결과.
"""
import re
import shutil
import urllib.parse

ENV = "/home/ubuntu/STA/.env"
shutil.copy(ENV, ENV + ".bak-cutover")

env = open(ENV, newline="").read().replace("\r\n", "\n").replace("\r", "\n")
env = re.sub(r"(?<!\n)LOCAL_PG_PASSWORD=", "\nLOCAL_PG_PASSWORD=", env)

m = re.search(r"^LOCAL_PG_PASSWORD=(.*)$", env, re.M)
assert m, "LOCAL_PG_PASSWORD 줄을 찾지 못함"
pw = m.group(1).strip()
assert pw, "LOCAL_PG_PASSWORD 값이 비어 있음"

old = re.search(r"^DATABASE_URL=(.*)$", env, re.M).group(1).strip()
newurl = "postgresql://postgres:%s@pg:5432/rendit" % urllib.parse.quote(pw, safe="")
env = re.sub(r"^DATABASE_URL=.*$", "DATABASE_URL=" + newurl, env, flags=re.M)

if "SUPABASE_DATABASE_URL=" not in env and not old.startswith("postgresql://postgres:"):
    env = env.rstrip("\n") + "\nSUPABASE_DATABASE_URL=" + old + "\n"

open(ENV, "w", newline="\n").write(env)
print("ok: DATABASE_URL -> pg:5432/rendit (백업: .env.bak-cutover)")
