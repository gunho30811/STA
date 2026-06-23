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
| `basic_options` | jsonb | 기본옵션 종류 배열 | `["냉장고","세탁기","에어컨"]` |
| `extra_options` | jsonb | 추가옵션 종류 배열 | `["전자레인지","건조기"]` |

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
| `booked_days_1m` ★ | int | 오늘 ~ +1개월 예약 일수 | `12` |
| `booked_days_2m` | int | 오늘 ~ +2개월 예약 일수 | `20` |
| `booked_days_3m` | int | 오늘 ~ +3개월 예약 일수 | `33` |

### 1.7 인근 지하철역 (지도 기준)
> 매물 좌표 기준 반경 내 지하철역. count + 역명 배열.

| 컬럼 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `station_500m_count` | int | 반경 500m 내 역 수 | `1` |
| `station_500m_names` | jsonb | 반경 500m 내 역명 | `["주엽역"]` |
| `station_1km_count` | int | 반경 1km 내 역 수 | `3` |
| `station_1km_names` | jsonb | 반경 1km 내 역명 | `["주엽역","대화역","킨텍스역"]` |

---

## 2. `naver_listings` — 네이버부동산 오피스텔 월세

기준 예시: 부천 소사구 오피스텔 월세 500/42 매물 (매물번호 2633302891)

### 2.1 식별 / 기본
| 컬럼 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `article_no` ★ | bigint PK | 네이버 매물번호 | `2633302891` |
| `url` | text | 상세 URL | — |
| `building_type` | text | 매물 종류 | `오피스텔` |
| `confirmed_at` | date | 확인매물 날짜 | `2026-06-20` |
| `posted_at` | date | 최초 게재일 | `2026-06-20` |
| `description` | jsonb | 매물소개(구조화, 긴 비정형 텍스트 제외) | `{"제공":"매경부동산","최초게재":"2026-06-20"}` |

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
| `jibun_address` ★ | text | 위치(지번) | `경기도 부천시 소사구 괴안동 113-6` |
| `road_address` ★ | text | 위치(도로명) | `경기도 부천시 소사구 경인로 509` |
| `building_name` ★ | text | 단지/건물명 (있으면) | `우남타워` |
| `lat` ★ | double | 위도 | — |
| `lng` ★ | double | 경도 | — |
| `building_use` | text | 건축물용도 | `업무시설` |
| `approval_date` | date | 사용승인일 | `2002-04-19` |
| `building_age` | int | 연차 | `25` |
| `households` | int | 세대수 | `48` |
| `households_same_area` | int | 해당 면적 세대수 | `6` |
| `entrance_type` | text | 현관구조 | `복도식` |
| `heating` | text | 난방 | `개별난방 / 도시가스` |
| `parking_total` | int | 총 주차대수 | `48` |
| `parking_per_household` | double | 세대당 주차 | `1` |
| `floor_area_ratio` | int | 용적률(%) | `775` |
| `building_coverage_ratio` | int | 건폐율(%) | `79` |
| `builder` | text | 건설사 | `우남건설(주)` |
| `dong_count` | int | 동수 | `1` |

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
| `school_name` | text | 배정 초등학교 | `부천부안초등학교` |
| `school_type` | text | 공립/사립 | `공립` |
| `school_distance_m` | int | 거리(m) | `821` |
| `subway_station` ★ | text | 가장 가까운 지하철역 | `역곡역` |
| `subway_line` | text | 노선 | `1호선` |
| `subway_distance_m` | int | 거리(m) | `81` |
| `bus_routes` | jsonb | 버스 노선(종류별) | `{"일반":["10","12"],"마을":["013"]}` |

### 2.7 같은 건물 통계 (수집/통합 단계 계산)
| 컬럼 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `same_building_same_area_count` ★ | int | 같은 건물(`building_name`)에서 **같은 면적**으로 올라온 매물 수 | `3` |

---

## 3. 비정형 / 제외 규칙
- 긴 자유 서술(매물 상세 설명문 등)은 컬럼으로 넣지 않는다. 구조화 가능한 항목만 `description`(jsonb)으로.
- 실거래가 위젯, 광고/배너, 안내 문구는 수집 대상 아님.
- 오류 데이터(예: 월세 100억) 가드는 통합 단계에서 `rent BETWEEN 5 AND 2000` 등으로 거른다.

## 4. 변경 이력
- 2026-06-23: 최초 작성. 네이버/삼삼 수집 목표 스키마 정의(target schema).
  현 Supabase 스키마와 차이가 있을 수 있으며, 수집 코드가 이 계약에 맞춰 채워가는 것이 목표.
