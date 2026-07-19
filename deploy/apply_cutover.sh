#!/bin/bash
# [1회 실행] 웹을 Supabase -> 서버 내부 pg로 전환하는 최종 적용 스크립트.
#   1) pg의 postgres 비밀번호를 .env의 LOCAL_PG_PASSWORD 값으로 정렬
#   2) DATABASE_URL -> 로컬 pg (Supabase URL은 SUPABASE_DATABASE_URL로 보존)
#   3) 웹 컨테이너 재시작
#   4) 동기화(6시간)·사용자백업(매일 04:40) 크론 설치
# 실행: ssh ubuntu@서버 "cd ~/STA && git pull && bash deploy/apply_cutover.sh"
set -e
DEPLOY=/home/ubuntu/STA/deploy

B=$(grep '^LOCAL_PG_PASSWORD=' /home/ubuntu/STA/.env | cut -d= -f2-)
[ -n "$B" ] || { echo "LOCAL_PG_PASSWORD 없음 — .env 확인 필요"; exit 1; }

sudo docker exec pg psql -U postgres -d rendit -c "ALTER USER postgres PASSWORD '$B'"

python3 $DEPLOY/cutover_env.py

cd /home/ubuntu/STA && sudo docker compose up -d --force-recreate web

chmod +x $DEPLOY/sync_from_supabase.sh $DEPLOY/backup_users.sh
sudo tee /etc/cron.d/rendit-sync >/dev/null <<EOF
0 */6 * * * root $DEPLOY/sync_from_supabase.sh
40 4 * * * root $DEPLOY/backup_users.sh
EOF
sudo chmod 644 /etc/cron.d/rendit-sync

echo "ALL DONE - 사이트가 로컬 DB로 전환되었습니다"
