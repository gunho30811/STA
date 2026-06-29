# 2026-06-29 — Vercel 느림/hang/500 상세 분석 & 수정

배포(sta-gray.vercel.app) 후 "첫 진입 30~60초 멈춤 / Internal Server Error / 전반적으로 느림".
원인을 4개로 분리해 수정. 각 항목 **증상 → 진단 → 원인 → 수정 → 검증** 기록.

---

## 공통 배경: 로컬은 멀쩡, Vercel만 느린 이유
- 로컬은 프로세스가 계속 살아있어(데이터 1회 로딩 후 메모리 상주) 비용이 안 보임.
- Vercel은 **서버리스**: 트래픽 없으면 함수가 잠들고(cold), 첫 요청에 **새 프로세스로 전 모듈 재import**.
  "import 시점의 무거운 일"을 콜드스타트마다 반복.
- 진단 결정타: `/auth/login 1차 30s(타임아웃) → 2차 0.33s` = 전형적 콜드스타트.

---

## 원인 1) import 시점에 데이터 전부 로딩 (hang)
- 진단: `portal.py`가 3뷰어 import → 각 앱이 모듈 최상단에서 즉시 로딩.
  gangnam `LISTINGS=_load()`(22MB jsonl), samsam `load_listings()`(DB)+`_load_matches()`, profit `load_profit()`(CSV).
- 수정: **lazy 로딩**. 즉시 로딩 → 캐시 함수로 변경(첫 호출 때만).
  ```python
  _CACHE=None
  def L():
      global _CACHE
      if _CACHE is None: _CACHE=_load()
      return _CACHE
  ```
  gangnam `LISTINGS→L()`, profit `PROFIT→P()`(단어경계로 PROFIT_MAP 보존), samsam `LISTINGS→L()/SOURCE→SRC()/MATCHES→M()`.
- 검증: portal import **30s+ → 0.36s**.

## 원인 2) init_db가 콜드스타트마다 DDL → ALTER 락 hang
- 진단: `init_auth`가 앱마다 `init_db()` 호출 → 콜드스타트당 최대 4회. 매번 CREATE/INDEX +
  **ALTER TABLE ADD COLUMN**(ACCESS EXCLUSIVE 락). 다른 연결이 테이블 읽으면 ALTER가 락 대기 → hang.
- 수정: `_INITED` 가드(프로세스당 1회) + **스키마 있으면 DDL 전체 스킵**(`SELECT to_regclass('public.members')`
  로 확인 후 즉시 return). ALTER/CREATE는 빈 DB 신규설치 때만.
- 검증: `init_db 0.21s`(이전 hang).

## 원인 3) 연결 누수 + idle-in-transaction → 풀러 고갈 (500)
- 진단: pg8000/psycopg2가 첫 쿼리에서 트랜잭션 자동 시작. SELECT 후 commit/close 안 하면
  "idle in transaction"으로 연결이 묶임(락·풀러 슬롯 점유). `pg_stat_activity` 연결 22 중 idle 12.
  pg_terminate_backend는 풀러 롤 권한부족(42501)으로 불가 → 코드로 차단.
- 수정: `_Conn.__init__`에 **autocommit=True**(idle-in-transaction 원천 차단) + `auth.current_user` 연결 close
  + `vercel.json` maxDuration 60s.
- 검증: 이후 idle-in-transaction 미발생.

## 원인 4) ★ 서버 지역 불일치 — "전반적으로 느림"의 주범
- 진단: 응답 헤더 `X-Vercel-Id: icn1::iad1::...`
  → 사용자는 서울 엣지(icn1)로 들어오는데 **함수 실행은 iad1(미국 버지니아)**. DB는 **Supabase 서울(ap-northeast-2)**.
  ```
  사용자(한국) → 함수(미국 iad1) → DB(서울)  ← 모든 쿼리가 미국↔서울 태평양 왕복(~400ms/회)
  ```
  페이지마다 DB 쿼리 여러 개(로그인 members 조회, 삼삼 DB, init_db, current_user 등) → 왕복 누적으로 느림.
  웜 상태 0.3~1초에도 이 왕복이 포함, 콜드/쿼리 많은 페이지에선 가중.
- 수정: `vercel.json`에 **`"regions": ["icn1"]`** (서울 고정). Hobby 단일 region 지정 가능.
  ```
  사용자(한국) → 함수(서울) → DB(서울)   ← 전부 서울, DB 왕복 ~400ms → ~10ms
  ```
- 효과(예상): DB 의존 페이지 응답 대폭 단축. 사용자 체감 속도 개선.

---

## 최종 로컬 검증(원인1~3 수정 후)
```
portal import 0.36s / init_db 0.21s / login 0.45s / 랜딩 0.19s / profit 0.02s / samsam facets 0.81s / gangnam 0.19s
```
## 배포본 실측(수정 후, 웜)
```
profit/api/profit 0.30s / samsam/api/facets 0.50s / samsam/api/buildings 0.97s(568KB) / gangnam/api/* 0.26s
헤더에서 iad1 확인 → regions:icn1 로 수정(원인4).
```

## 배포
- PR #55: 원인 1~3(lazy/init_db 스킵/autocommit/maxDuration).
- 후속 커밋: 원인 4 (`regions:["icn1"]`).
- 적용 파일: `db.py`, `web/{gangnam,profit,samsam}_app.py`, `web/auth.py`, `vercel.json`.

## 원인 4 후속: 무료 플랜은 함수 지역 변경 불가 → 삼삼도 파일 서빙으로 우회
- `regions:["icn1"]` 적용해도 배포 후 여전히 `icn1::iad1`(함수 미국). **Hobby 플랜은 함수 지역 고정**
  (대시보드 Functions 탭에서도 변경 불가). 즉 함수는 미국에 묶임.
- 실측으로 느림의 정체 확인: **파일 기반 페이지는 빠르고(profit 0.26s, gangnam 0.46s) DB(서울) 쓰는
  페이지만 느림**(samsam facets 0.5s, buildings 0.9s, trend 2.36s, login 1.8s). 미국 함수↔서울 DB 왕복 탓.
- 해결: DB 이전/Pro 없이 — **삼삼도 profit/gangnam처럼 파일에서 읽게** 전환(미국 함수 안의 로컬 파일이라
  태평양 왕복 0). `pipeline/samsam/export_jsonl.py`로 samsam_listings(11MB)·samsam_snapshots(506KB)를
  `lab/`에 export(주간 크롤 후 재생성·커밋). `samsam_app`: EXPORT 파일 우선 로드(없으면 DB→샘플),
  `api_trend`도 스냅샷 파일 우선.
- 검증(로컬): samsam 출처=파일, buildings 0.9s→**0.06s**, trend 2.36s→**0.01s**.
- DB-온-핫패스는 이제 auth(로그인/회원관리)만 남음(빈도 낮음). 데이터 갱신 워크플로: 크롤 → export_jsonl
  → build_integrated → 커밋(자동 재배포).

## 반전: 진짜 배포 실패 원인은 vercel.json (파일 크기 아님)
- 증상: 성능 수정들이 배포돼도 사이트가 안 빨라짐. 알고 보니 **#55부터 모든 배포가 build failure**,
  마지막 성공은 #54(c5a535d). 즉 lazy/autocommit/region/파일서빙 전부 **배포 자체가 안 됐고** 옛 #54가 떠있었음.
- 원인: `vercel.json`의 `"functions":{maxDuration:60,memory:1024}`(#55) + `"regions":["icn1"]`(#56)이
  **Hobby 플랜에서 빌드를 깨뜨림**(허용 안 되는 설정 → 무시가 아니라 실패). 파일 33MB는 무관했음.
  → #57(파일서빙)을 용량 탓으로 오판해 되돌린 것도 잘못된 진단이었음.
- 수정: `vercel.json`을 최소 설정(rewrites+installCommand)으로 복구(#59) → **배포 성공**.
  그러자 그동안 막혔던 lazy/autocommit이 처음으로 실제 반영됨.
- 그 위에 삼삼 파일서빙 재적용(#60, 되돌리기를 되돌림) → 배포 성공 + 삼삼 빨라짐.
- 최종 배포본 실측(웜): 수익성 0.24s / 삼삼 facets 0.50s · trend 0.46s(이전 2.36s) · 건물 1.24s /
  강남 1.0s / 랜딩 1.5s. 삼삼 첫 로딩 3.8s→0.3~0.5s.
- 교훈: "배포했는데 안 바뀐다"면 **먼저 배포 state(success/failure)부터 확인**할 것. Hobby는
  vercel.json의 functions/regions 같은 Pro 전용 키에 민감(빌드 실패).

## 남은 후속 과제
- 강남 22MB jsonl은 콜드 인스턴스마다 1회 파싱 — DB 이전 또는 서버사이드 페이지네이션으로 개선 여지.
- auth.py 나머지 라우트도 연결 명시 close 권장(autocommit으로 치명 누수는 차단됨).
- 트래픽 늘면 Supabase transaction pooler 설정/한도 모니터링.
