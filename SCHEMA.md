# 데이터 스키마 계약 (SCHEMA contract)

이 문서는 **두 수집원(네이버부동산 / 삼삼엠투)이 각자 책임지고 채우는 컬럼**과
**통합 단계가 읽기만 하는 인터페이스**를 정의한다.

원칙
- 각 수집원은 **자기 테이블에만 write** 한다. (네이버 → `naver_listings`, 삼삼 → `samsam_listings`)
- 통합 단계(`pipeline/integrate/build_integrated.py`)는 두 테이블을 **읽기만** 한다.
- 컬럼을 추가/변경하면 **이 문서를 먼저 고치고**(계약 우선), 그 다음 코드를 바꾼다.
- 타입은 Postgres(Supabase) 기준. 금액 단위는 컬럼별 비고에 명시.
- `★` = 매칭/수익성의 **핵심 컬럼**. 비우면 안 됨.

담당
| 테이블 | 수집원 | 담당 | 수집 코드 |
|--------|--------|------|-----------|
| `naver_listings` | 네이버부동산 | **gunho** | `pipeline/naver/` |
| `samsam_listings` | 삼삼엠투 | **Soojung** | `pipeline/samsam/` |

---

## 1. `samsam_listings` — 삼삼엠투 단기임대

기준 예시: <https://web.33m2.co.kr/guest/room/102951> (주엽역 풀옵션 초역세권)

### 1.1 식별 / 기본
| 컬럼 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `room_id` ★ | bigint PK | 삼삼 매물 번호 (URL `/room/{id}`) | `102951` |
| `url` | text | 상세 URL | `https://web.33m2.co.kr/guest/room/102951` |
| `name` | text | 매물명 | `주엽역 풀옵션 초역세권` |
| `building_type` | text | 건물 유형 | `오피스텔` |

### 1.2 주소 / 위치
| 컬럼 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `road_address` ★ | text | 도로명 주소 (층 제외) | `경기도 고양시 일산서구 중앙로 1455` |
| `jibun_address` ★ | text | 지번 주소 (층 제외) | `경기도 고양시 일산서구 주엽동 115 대우시티프라자` |
| `building_name` ★ | text | 건물명 (지번에서 파싱) | `대우시티프라자` |
| `floor` ★ | int | 층수 (도로명/지번 끝 `N층`에서 파싱) | `5` |
| `lat` ★ | double | 위도 | `37.6737929` |
| `lng` ★ | double | 경도 | `126.7613738` |

### 1.3 면적 / 구조
| 컬럼 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `area_m2` ★ | double | 전용 면적(㎡) | `46` |
| `area_pyeong` | double | 전용 면적(평) | `14` |
| `rooms` | int | 방 개수 | `1` |
| `bathrooms` | int | 화장실 개수 | `1` |
| `kitchens` | int | 주방 개수 | `1` |
| `living_rooms` | int | 거실 개수 | `1` |
| `elevator` | bool | 엘리베이터 유무 | `true` |
| `parking` | bool | 주차 가능 여부 (`주차 불가능`→false) | `false` |

### 1.4 옵션
| 컬럼 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `basic_options` | jsonb | 기본옵션 — 상세 API `basicOptions`. **영문 코드** 배열 | `["REFRIGERATOR","WASHING_MACHINE","TV"]` |
| `extra_options` | jsonb | 추가옵션 — 상세 API `additionalOptions`. **영문 코드** 배열 | `["MICROWAVE","DRYER"]` |

> 옵션값은 삼삼 API가 주는 **영문 코드**(TV, REFRIGERATOR, AIR_CONDITIONER …) 그대로 저장한다.
> 한글 표시명 매핑은 뷰어(`web/samsam_app.py` `OPTION_KO`)에서 한다. 상세 API는 `missingOptions`(해당
> 매물에 **없는** 옵션 코드)도 주지만 현재 적재 안 함 — "옵션 없는 집"은 (관측된 전체 옵션 − 보유 옵션)으로 계산.

### 1.5 임대료 (단위: 원/주)
| 컬럼 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `rent_total_weekly` ★ | int | 임대료 총합(주) = 임대료+관리비 | `450000` |
| `rent_weekly` ★ | int | 임대료(주) | `360000` |
| `maintenance_weekly` ★ | int | 관리비(주) | `90000` |

### 1.6 예약 현황 (수집 기준일 `collected_at` 시점)
> 입주 캘린더에서 **오늘부터 N개월 구간 중 예약(차있는) 일수**를 센다.

| 컬럼 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `collected_at` ★ | date | 예약일수 산정 기준일 | `2026-06-23` |
| `booked_days_1m` ★ | int | 오늘 ~ +1개월 예약(차있는) 일수 | `12` |
| `booked_days_2m` | int | 오늘 ~ +2개월 예약 일수 | `20` |
| `booked_days_3m` | int | 오늘 ~ +3개월 예약 일수 | `33` |
| `blocked_days_1m` | int | 오늘 ~ +1개월 **막힘(disable)** 일수. 공실률 보정용(가용일 = 30 − 막힘일) | `4` |

### 1.7 인근 지하철역 (지도 기준)
> 매물 좌표 기준 반경 내 지하철역. count + 역명 배열.

| 컬럼 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `station_500m_count` | int | 반경 500m 내 역 수 | `1` |
| `station_500m_names` | jsonb | 반경 500m 내 역명 | `["주엽역"]` |
| `station_1km_count` | int | 반경 1km 내 역 수 | `3` |
| `station_1km_names` | jsonb | 반경 1km 내 역명 | `["주엽역","대화역","킨텍스역"]` |

### 1.8 지역(주소에서 파싱)
> 도/구 단위 집계·매칭 인덱싱용. `jibun_address`(없으면 `road_address`)에서 파싱.

| 컬럼 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `sido` | text | 시/도 | `경기도` |
| `sigungu` | text | 시/군/구 (구 분리 시 `고양시 일산서구`) | `고양시 일산서구` |
| `dong` | text | 동/읍/면/리 | `주엽동` |

---

## 2. `naver_listings` — 네이버부동산 매물 월세

기준 예시: 부천 소사구 오피스텔 월세 500/42 매물 (매물번호 2633302891)

**대상 매물종류 (6종, 전부 `tradeType=B2` 월세 한정)**: 아파트(`APT`) · 오피스텔(`OPST`) ·
빌라(`VL`) · 원룸(`OR`) · 단독/다가구(`DDDGG`) · 상가(`SG`).

> **단지형 vs 비단지형**: 네이버 상세 API가 단지번호(`hscpNo`)를 주는 매물(아파트/오피스텔, `isComplex=true`)과
> 안 주는 매물(빌라/원룸/단독다가구/상가, `isComplex=false`)은 채워지는 컬럼이 다르다. 비단지형은
> `complexes/{c}`·`complexes/{c}/schools` API 자체를 호출할 대상(`hscpNo`)이 없어서 **단지 기반 컬럼이 전부
> NULL**이 된다 — 아래 각 표에 `[단지형만]` 표시. (직접 API 호출로 확인: VL/OR/DDDGG/SG 샘플 모두
> `hscpNo=None`, `articleExistTabs`에 `schools` 없음.)

### 2.1 식별 / 기본
| 컬럼 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `article_no` ★ | bigint PK | 네이버 매물번호 | `2633302891` |
| `url` | text | 상세 URL | — |
| `building_type_code` ★ | text | 매물종류 코드(`realestateTypeCode`) | `OPST` |
| `building_type` | text | 매물 종류명 | `오피스텔` |
| `confirmed_at` | date | 확인매물 날짜 | `2026-06-20` |
| `posted_at` | date | 최초 게재일 | `2026-06-20` |
| `summary` ★ | text | **매물 한줄 소개(요약 칸 원문 전체)** — 절대 자르거나 누락하지 말 것 | `풀옵션,복층형원룸오피스텔,역곡역 도보3분,역곡남부의 모든 생활인프라집중.` |
| `summary_tags` | jsonb | `summary`를 쉼표로 분리한 태그 배열(선택) | `["풀옵션","복층형원룸오피스텔","역곡역 도보3분","역곡남부의 모든 생활인프라집중"]` |
| `tags` | jsonb | 네이버 매물 구조화 태그(`tagList`) — `summary_tags`와 별개 출처 | `["25년이상","올수리","화장실한개","소형평수"]` |
| `description` | jsonb | 게재 메타(제공처·게재일 등 구조화 항목만). 긴 자유 서술 본문은 제외 | `{"제공":"매경부동산","최초게재":"2026-06-20"}` |

### 2.2 가격 (단위: 만원)
| 컬럼 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `deposit` ★ | int | 보증금 | `500` |
| `rent_monthly` ★ | int | 월세 | `42` |
| `maintenance_monthly` ★ | int | 관리비 | `10` |
| `maintenance_type` | text | 관리비 부과기준 | `정액관리비` |

### 2.3 면적 / 구조
| 컬럼 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `area_contract_m2` | double | 계약면적(㎡) | `39.86` |
| `area_exclusive_m2` ★ | double | 전용면적(㎡) | `20.28` |
| `exclusive_ratio` | int | 전용률(%) | `51` |
| `floor_current` ★ | int | 해당 층 | `9` |
| `floor_total` | int | 총 층 | `10` |
| `rooms` | int | 방 수 | `1` |
| `bathrooms` | int | 욕실 수 | `1` |
| `direction` | text | 향(안방 기준) | `서향` |
| `duplex` | bool | 복층 여부 | `true` |
| `move_in` | text | 입주가능일 | `즉시입주 협의 가능` |
| `facilities` | jsonb | 시설 정보 | `["벽걸이에어컨","엘리베이터"]` |

### 2.4 주소 / 단지 정보
| 컬럼 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `jibun_address` ★ | text | 위치(지번). 단지형은 `complexes/{c}`의 지번, 비단지형은 `exposureAddress` 그대로(번지 없이 동까지만 노출되는 경우도 있음, 예: 상가) | `경기도 부천시 소사구 괴안동 113-6` |
| `road_address` ★ | text | 위치(도로명). **`[단지형만]`** — 비단지형은 도로명 자체를 안 줘서 NULL | `경기도 부천시 소사구 경인로 509` |
| `building_name` ★ | text | 단지/건물명 (있으면) | `우남타워` |
| `bldg_dong` | text | 건물 내 동(棟) 라벨 (있으면, 단지 전체 동수인 `dong_count`와 다름) | `301동` |
| `lat` ★ | double | 위도 | — |
| `lng` ★ | double | 경도 | — |
| `building_use` | text | 건축물용도. `[단지형만]` | `업무시설` |
| `approval_date` | date | 사용승인일. `[단지형만]` | `2002-04-19` |
| `building_age` | int | 연차. `[단지형만]` | `25` |
| `households` | int | 세대수. `[단지형만]` | `48` |
| `households_same_area` | int | 해당 면적 세대수. `[단지형만]` | `6` |
| `entrance_type` | text | 현관구조. 비단지형은 보통 NULL(샘플 확인: 빌라/원룸/단독다가구/상가 전부 None) | `복도식` |
| `heating` | text | 난방. `[단지형만]` | `개별난방 / 도시가스` |
| `parking_total` | int | 총 주차대수. `[단지형만]` | `48` |
| `parking_per_household` | double | 세대당 주차. `[단지형만]` | `1` |
| `floor_area_ratio` | int | 용적률(%). `[단지형만]` | `775` |
| `building_coverage_ratio` | int | 건폐율(%). `[단지형만]` | `79` |
| `builder` | text | 건설사. `[단지형만]` | `우남건설(주)` |
| `dong_count` | int | 동수. `[단지형만]` | `1` |

### 2.5 중개사
| 컬럼 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `agent_office` ★ | text | 중개소명 | `옹고집공인중개사사무소` |
| `agent_name` | text | 중개사명 | `이영미` |
| `agent_phone` ★ | jsonb | 전화번호 배열 | `["032-351-3301","010-9920-3771"]` |
| `agent_address` | text | 중개소 위치 | `경기 부천시 원미구 역곡동 107-8 1층일부` |
| `agent_reg_no` | text | 등록번호 | `41190-2023-00089` |
| `agent_owner_confirmed_3m` | int | 최근 3개월 집주인확인 건수 | `37` |
| `broker_fee_max` | int | 중개보수 최대(만원, VAT별도) | `13` |
| `broker_fee_rate` | double | 상한 요율(%) | `0.4` |

### 2.6 학교 / 교통
| 컬럼 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `school_name` | text | 배정 초등학교. `[단지형만]` — 비단지형은 `articleExistTabs`에 `schools` 탭 자체가 없어 호출 대상 없음 | `부천부안초등학교` |
| `school_type` | text | 공립/사립. `[단지형만]` | `공립` |
| `school_walk_min` | int | 도보 분(네이버 API 제공). `[단지형만]` | `11` |
| `school_student_per_teacher` | double | 교사 1인당 학생수. `[단지형만]` | `14.2` |
| `subway_station` ★ | text | 가장 가까운 지하철역(`N역` 형태) | `언주역` |
| `subway_distance_m` | int | 거리(m) | `406` |
| `subway_500m` | jsonb | 반경 500m 내 역명(`N역` 형태) | `["언주역"]` |
| `subway_1km` | jsonb | 반경 1km 내 역명(`N역` 형태) | `["언주역","선정릉역","역삼역","학동역"]` |
| `subway_walk_min` | int | (참고) 네이버 도보 분 | `6` |

> **지하철 출처**: 네이버 상세 API는 역명/거리(m)를 안 주고 `walkingTimeToNearSubway`(도보 분)만 준다.
> `subway_station`/`subway_distance_m`/`subway_500m`/`subway_1km` 는 매물 좌표(lat/lng)와
> 역 좌표 테이블(`data/subway_stations.csv`, 수도권 589역)로 **직접 계산**한다 — `pipeline/naver/subway.py`.
> 노선(`subway_line`)은 역 좌표표에 노선정보가 없어 보류(필요 시 노선맵 별도 추가). 비수도권은 역표 확장 필요.

### 2.7 같은 건물 통계 (수집/통합 단계 계산)
| 컬럼 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `same_building_same_area_count` ★ | int | 같은 건물(`building_name`)에서 **같은 면적**으로 올라온 매물 수. 비단지형은 단지 그룹이 없어 항상 `1` | `3` |

> **"같은 면적" 정의**: 면적차 **≤ 1평(약 3.3㎡)** 이면 같은 면적으로 취급한다.
> 기준 면적은 `area_exclusive_m2`(전용면적). 즉 `abs(전용면적 − 다른매물 전용면적) ≤ 3.3㎡`.
>
> **출처**: `articleAddition.sameAddrCnt` (상세 API). 같은 건물이라도 **평형별로 카운트가 따로** 잡힌다
> (예: 상지카일룸블랙 72OD=3건, 57OC=33건). 그대로 읽으면 됨.
> 단지형(아파트/오피스텔)은 `hscpNo` 기준으로 잡히는 걸로 보이지만, 비단지형(빌라/원룸/단독다가구/상가)도
> `hscpNo` 없이 **지번/주소 기준으로 같이 카운트되는 듯** — 100건씩 샘플 검증 결과 비단지형도 2~5인 경우가
> 적지 않음(예: 빌라 17%, 상가 16%가 1 아님). ~~"비단지형은 항상 1"~~ 은 1건짜리 샘플로 잘못 판단한 것이었음
> (2026-06-25 정정). 정확한 카운트 기준(주소 매칭 알고리즘)은 네이버가 비공개라 추정만 가능.

---

## 3. 비정형 / 제외 규칙
- **매물 한줄 소개(요약 칸)는 반드시 `summary`에 원문 전체를 담는다.** 짧은 비정형 텍스트지만 핵심 정보(역세권/옵션/구조)라 자르지 말 것.
- 그 외 **긴** 자유 서술(매물 상세 설명 본문 등)은 컬럼으로 넣지 않는다. 구조화 가능한 메타 항목만 `description`(jsonb)으로.
- 실거래가 위젯, 광고/배너, 안내 문구는 수집 대상 아님.
- 오류 데이터(예: 월세 100억) 가드는 통합 단계에서 `rent BETWEEN 5 AND 2000` 등으로 거른다.
  단, 상가(`SG`)는 보증금/월세 분포가 주거용과 달라서 같은 상한을 그대로 적용하면 안 될 수 있음 — 통합
  단계에서 `building_type_code`별로 가드 값을 분리할지 검토 필요.

## 3.1 통합(매칭) 단계 규칙 — `pipeline/integrate/build_integrated.py`
- **타입 매칭 범위**: 삼삼은 오피스텔/원룸 단기임대 중심이라 네이버는 `building_type_code='OPST'`만 매칭에
  사용한다(아파트/상가 등 오매칭 방지). 향후 타입별 매칭 확장 시 이 필터를 조정.
- **보증금 정규화(전월세 환산)**: 같은 평형이라도 네이버 매물의 보증금/월세 조합이 제각각이라(보증금↑→월세↓)
  보증금 큰 매물을 그대로 쓰면 단기임대 순수익이 과대평가된다. 모든 네이버 매물을 **환산월세**로 통일해서
  비교: `환산월세 = 월세 + 보증금(만원) × 전월세전환율 / 12` (전환율 연 6%, `CONV_RATE`).
  순수익은 `1달실현수익 − (환산월세 + 관리비)`. `--max-deposit` 으로 특정 보증금 이하만 쓰는 하드 필터도 선택 가능.
- **같은 오피스텔 삼삼 매물 수(`삼삼동일건물매물수`)**: "이 건물에 삼삼 단기임대가 몇 개 올라와 있는지".
  건물명+동 기준(건물명 없으면 좌표 ~11m)으로 묶어 카운트(자기 포함). `동삼삼매물수`(동 단위)와 구분.

## 4. 변경 이력
- 2026-06-23: 최초 작성. 네이버/삼삼 수집 목표 스키마 정의(target schema).
  현 Supabase 스키마와 차이가 있을 수 있으며, 수집 코드가 이 계약에 맞춰 채워가는 것이 목표.
- 2026-06-25: `naver_listings` 대상을 오피스텔 월세 단일종에서 **아파트/오피스텔/빌라/원룸/단독다가구/상가**
  6종(전부 월세)으로 확장. `building_type_code` 컬럼 추가. 단지형(아파트/오피스텔, `hscpNo` 있음)과
  비단지형(빌라/원룸/단독다가구/상가, `hscpNo` 없음)의 컬럼 채움 여부 차이를 실제 API 호출로 확인해서
  각 표에 `[단지형만]` 표시 추가 (`road_address`/`building_age`/`households`/`parking_*`/
  `floor_area_ratio`/`building_coverage_ratio`/`builder`/`dong_count`/`school_*`/`same_building_same_area_count`
  실질값). `lab/crawl_gangnam_all_types.py`(강남구 시범 수집)로 검증.
