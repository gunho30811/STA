#!/bin/bash
# Supabase -> 로컬 pg 데이터 동기화 (6시간마다 cron이 실행).
# 크롤러(GHA/GUI)가 Supabase에 쓰는 데이터 테이블만 덮어씀.
# 회원·채팅·접속 테이블은 로컬 pg가 원본이므로 절대 건드리지 않음.
set -e
exec >> /home/ubuntu/sync_supabase.log 2>&1
echo "=== $(date '+%F %T') sync start"

# .env의 SUPABASE_DATABASE_URL -> PG* env 파일 (pg_dump용, 세션모드 5432).
# URI를 pg_dump에 직접 주면 실패했던 이력이 있어 반드시 컴포넌트로 분해.
python3 - <<'PY'
import os, re, urllib.parse
env = open('/home/ubuntu/STA/.env').read()
u = urllib.parse.urlsplit(
    re.search(r'^SUPABASE_DATABASE_URL=(.*)$', env, re.M).group(1).strip())
with open('/home/ubuntu/.supa_pgenv', 'w') as f:
    f.write(f"PGHOST={u.hostname}\nPGPORT=5432\n"
            f"PGUSER={urllib.parse.unquote(u.username)}\n"
            f"PGPASSWORD={urllib.parse.unquote(u.password)}\n"
            f"PGDATABASE={u.path.lstrip('/')}\n")
os.chmod('/home/ubuntu/.supa_pgenv', 0o600)
PY

TABLES="listings naver_listings regions crawl_state net_profit kv_cache samsam_listings samsam_snapshots samsam_churn samsam_live samsam_booking_events"
TFLAGS=""
for t in $TABLES; do TFLAGS="$TFLAGS -t public.$t"; done

# 새 테이블(예약 이벤트)이 로컬에 아직 없으면 생성 — TRUNCATE가 실패하지 않게.
docker exec pg psql -U postgres -d rendit -q -c "CREATE TABLE IF NOT EXISTS samsam_booking_events(
  id BIGSERIAL PRIMARY KEY, room_id BIGINT, detected_at TEXT, stay_date TEXT, change TEXT, lead_days INT);
  CREATE INDEX IF NOT EXISTS ix_sbe_detected ON samsam_booking_events(detected_at);"

# 덤프는 Supabase env-file로, 복원은 '깨끗한' exec로 (PGHOST 누출로 복원이
# Supabase를 향했던 과거 사고 재발 방지 — 복원 단계엔 env를 절대 주입하지 않음)
docker exec --env-file /home/ubuntu/.supa_pgenv pg pg_dump --data-only $TFLAGS -f /tmp/sync_data.sql
docker exec pg psql -U postgres -d rendit -v ON_ERROR_STOP=1 -1 \
    -c "TRUNCATE ${TABLES// /,}" -f /tmp/sync_data.sql
docker exec pg rm -f /tmp/sync_data.sql
rm -f /home/ubuntu/.supa_pgenv
echo "=== $(date '+%F %T') sync done"
