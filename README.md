# 네이버부동산 오피스텔 월세 크롤러 & 삼삼엠투 단기임대 수익성 분석

수도권/전국 오피스텔 월세 매물을 네이버부동산에서 수집하고, 삼삼엠투(단기임대) 데이터와
같은 건물·평수로 매칭해 **임대인 관점의 수익성**을 분석·검색하는 로컬 도구.

웹앱 두 개로 구성됩니다.

| 앱 | 포트 | 내용 |
|----|------|------|
| `web/app.py` | 5000 | 네이버 오피스텔 **월세 매물 검색** (서울/경기/인천, 시·군·구·동 필터) |
| `web/profit_app.py` | 5001 | 삼삼엠투 × 네이버 **단기임대 수익성** 검색 (전국, 건물유형/방수/지역/순수익 필터) |

---

## 1. 빠른 시작 (뷰어만 보기)

데이터(`data/`)는 이미 들어 있으므로, 보기만 할 거면 Flask만 있으면 됩니다.

```bash
# Python 3.10+ 권장
pip install flask

python web/app.py          # → http://127.0.0.1:5000  (네이버 매물 검색)
python web/profit_app.py   # → http://127.0.0.1:5001  (수익성 분석)
```

브라우저에서 위 주소로 접속. (두 앱은 포트가 달라 동시에 띄워도 됨)

---

## 2. 전체 설치 (데이터까지 직접 갱신하려면)

```bash
pip install -r requirements.txt
python -m playwright install chromium     # 크롤용 크롬 1회 다운로드
```

- `flask` — 웹 뷰어 (필수)
- `requests` — 삼삼엠투 API 호출 (갱신 시)
- `playwright` (+chromium) — 네이버 크롤 (갱신 시)

---

## 3. 폴더 구조

```
.
├─ README.md                 ← 이 문서
├─ requirements.txt
├─ .env.example              ← 삼삼 로그인 환경변수 양식 (갱신 시 .env 로 복사해 사용)
├─ db.py                     ← Supabase(PostgreSQL) 연결 헬퍼 (web + pipeline 공유)
├─ SCHEMA.md                 ← 수집 데이터 스키마 계약 (네이버/삼삼 컬럼 정의)
│
├─ web/                      웹앱 (둘 다 db.py·templates·data 를 공유)
│   ├─ app.py                 시스템 A: 네이버 매물 뷰어
│   └─ profit_app.py          시스템 B: 수익성 뷰어
│
├─ pipeline/                 데이터 수집/가공 스크립트 (수집원별로 분리)
│   ├─ naver/                 ▣ 네이버부동산 (담당: gunho)
│   │   ├─ crawler.py             네이버 크롤러 (Playwright) — 시스템 A 용
│   │   ├─ get_targets.py         ① 비수도권 크롤 대상 시군구 추출
│   │   ├─ crawl_nonseoul.py      ② 비수도권 네이버 크롤 (crawler.py 재사용)
│   │   └─ enrich_nonseoul.py     ③ 비수도권 관리비 보강
│   ├─ samsam/                ▣ 삼삼엠투 (담당: Soojung)
│   │   └─ classify_btype.py      ④ 삼삼 건물유형(아파트/빌라) 분류
│   └─ integrate/            ▣ 통합 (양쪽 테이블 읽기 전용)
│       ├─ build_integrated.py    ⑤ 매칭 → 최종 CSV 생성
│       ├─ build_integrated_sqlite.py   (레거시: SQLite 버전)
│       └─ migrate_to_supabase.py       (1회성: SQLite→Supabase 이전)
│
├─ templates/
│   ├─ index.html            (네이버 뷰어 UI)
│   └─ profit.html           (수익성 뷰어 UI)
│
└─ data/                     모든 데이터 (*.db, *.jsonl 은 git 미추적 — 로컬 크롤 후 생성)
    ├─ net_profit_integrated.csv ★ 최종 수익성 결과 (profit_app 이 읽음)
    ├─ btype_map.json            삼삼 rid→건물유형
    ├─ targets.json              비수도권 크롤 대상
    └─ landlord_best_dong.csv / vacancy_by_gu.csv / by_station.csv  (보조 탭)

    ※ 크롤 후 자동 생성되는 파일 (git 미추적)
    ├─ naver_opst.db / naver_nonseoul.db   네이버 매물 (Supabase로 이전됨 — 레거시)
    ├─ officetel_raw.jsonl                 삼삼 오피스텔 원본
    └─ oneroom_raw.jsonl                   삼삼 원룸 원본
```

---

## 4. 데이터 수집 방법 (핵심)

### 네이버부동산
- `requests` 직접 호출은 TLS 핑거프린팅으로 차단됨(타임아웃).
- 그래서 **Playwright(진짜 크롬)로 new.land.naver.com 을 띄워 Authorization 토큰을 확보하고,
  브라우저 내부에서 `fetch()` 로 비공개 API를 호출**한다. (`pipeline/naver/crawler.py`)
- 대상: `realEstateType=OPST`(오피스텔), `tradeType=B2`(월세).
- 지역 코드(cortarNo) 재귀 드릴다운으로 시/도 → 구/시 → 동까지 분류.

### 삼삼엠투 (web.33m2.co.kr)
- 로그인(쿠키) 후 내부 API 호출. **레이트리밋 주의** — 천천히(스레드 낮게, 0.6s 간격), 403 시 대기.
- 원본 캐시는 `data/*.jsonl` 에 저장되며, 이미 받은 경우 재요청 없이 재사용.

---

## 5. 데이터 갱신(재크롤) 순서

> 시간이 걸리고 네트워크/계정이 필요. 보통은 동봉된 `data/` 그대로 쓰면 됩니다.

```bash
# (선택) 삼삼 로그인 정보 입력 — .env 에 미리 넣어두거나, 비워두면 실행 시 직접 입력 받음
copy .env.example .env   # 편집 후 SAMSAM_EMAIL / SAMSAM_PASSWORD 입력

# 시스템 A — 네이버 수도권 매물 갱신
python pipeline/naver/crawler.py                    # 서울/경기/인천 전체 (이어받기 지원)

# 시스템 B — 전국 수익성 재생성
python pipeline/naver/get_targets.py                # ① 비수도권 대상 시군구 추출 → data/targets.json
python pipeline/naver/crawl_nonseoul.py             # ② 비수도권 네이버 크롤 → Supabase
python pipeline/naver/enrich_nonseoul.py            # ③ 비수도권 관리비 보강
python pipeline/samsam/classify_btype.py            # ④ 삼삼 건물유형 분류 → data/btype_map.json
python pipeline/integrate/build_integrated.py       # ⑤ 매칭 → data/net_profit_integrated.csv
```

모든 크롤은 중단돼도 이미 받은 분은 건너뛰고 **이어받기** 됩니다.

---

## 6. 수익성 지표 정의 (단위: 만원)

- 1달실현수익 = 삼삼 주당요금 × (1달 예약일 / 7)
- 네이버÷삼삼주당 = 네이버 월총(월세+관리비) ÷ 삼삼 주당요금
- 현실효율 = 1달실현수익 ÷ 네이버 월세
- 순수익 = 1달실현수익 − (네이버 월세 + 관리비)
- 매칭조건: 같은 건물(좌표 ≤50m 또는 건물명 일치) + 면적차 ≤3㎡
- 네이버 관리비 미표기 시 평당 2만원으로 추정.

---

## 7. 주의 / 한계

- 데이터는 **수집 시점 스냅샷**(현재 2026-06 기준). 최신화하려면 5번 갱신.
- 네이버 매칭은 **오피스텔 시세 기준**. 비오피스텔(아파트/빌라)은 정확도가 낮으니 건물유형 필터로 분리해서 볼 것.
- 삼삼엠투 로그인 정보는 `.env` (git에서 제외됨)에 넣거나, 비워두면 실행 시 직접 입력 받습니다. `.env`를 절대 커밋/공유하지 마세요.
- 비공개 API 사용이므로 네이버/삼삼이 구조를 바꾸면 크롤러 수정이 필요할 수 있음. 과도한 요청은 차단됨.

---

## 8. 같은 망(LAN)에서 공유

두 앱 모두 `0.0.0.0` 으로 떠서 같은 네트워크 기기가 접속 가능. 방화벽 포트 개방 필요(관리자):

```powershell
New-NetFirewallRule -DisplayName "Officetel Web" -Direction Inbound -Protocol TCP -LocalPort 5000,5001 -Action Allow -RemoteAddress LocalSubnet
```

접속: `http://{내 LAN IP}:5000` 또는 `:5001`
