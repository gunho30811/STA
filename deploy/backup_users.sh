#!/bin/bash
# 로컬 pg가 원본인 사용자 데이터(회원·채팅·접속)를 매일 새벽 서버 내에 백업.
# 요일별 파일(users-1.sql ~ users-7.sql)로 7일 로테이션.
set -e
D=/home/ubuntu/backups
mkdir -p $D
T="members visitor_pings chat_logs samsam_chat_messages samsam_chat_rooms samsam_chat_outbox samsam_accounts"
F=""
for t in $T; do F="$F -t public.$t"; done
docker exec pg pg_dump -U postgres -d rendit --data-only $F -f /tmp/users.sql
docker cp pg:/tmp/users.sql $D/users-$(date +%u).sql
docker exec pg rm -f /tmp/users.sql
