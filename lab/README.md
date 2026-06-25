# lab/

운영 파이프라인(`pipeline/`)에 합치기 전에 빠르게 찔러보는 실험/단위테스트 공간.
**DB(Supabase)는 건드리지 않고** 결과를 로컬 파일로만 남긴다. 운영 코드가 안정화되면
해당 로직은 `pipeline/`으로 옮기고 여기선 지운다.

## 원칙

- 결과는 **`naver_listings` 스키마(SCHEMA.md) 그대로인 jsonl 파일 하나**로만 남긴다.
  목록(list)/상세(detail) 단계를 별도 파일로 쪼개지 않는다 — 운영 테이블도 하나(`naver_listings`)라
  로컬 산출물을 여러 개로 쪼개면 어떤 파일에 어떤 컬럼이 있는지 헷갈리기 쉽다.
- `pipeline/naver/`의 `NaverLand`(브라우저 인증)·`build_region_tree`·`crawl_detail.fetch_row`
  (상세+단지+학교 API + 좌표 역계산)를 그대로 재사용한다. 인증/매핑 로직을 lab에서 새로 만들지 않는다.
- 스크립트 실행 결과(jsonl/log)는 보통 git에 안 올린다(아래 "데이터 커밋" 참고). 코드만 커밋 대상.

## 파일

| 파일 | 설명 |
|------|------|
| `crawl_naver_gangnam.py` | 강남구 한정, 네이버 6개 타입(아파트/오피스텔/빌라/원룸/단독다가구/상가) 전수 수집 — 목록 스캔으로 매물번호를 모은 뒤 매물마다 상세 보강까지 한 번에 처리. |
| `naver_listings_gangnam.jsonl` | 위 스크립트 출력. `naver_listings`(SCHEMA.md) 컬럼 그대로, 강남구 6타입 전체. |

## 사용

```bash
python lab/crawl_naver_gangnam.py                 # 타입당 최대 60페이지(production 기본값)
python lab/crawl_naver_gangnam.py --max-pages 5    # 빠른 샘플 테스트
```

## 데이터 커밋

이번 강남구 전수 수집 결과(`naver_listings_gangnam.jsonl`)는 다중 타입 확장이 제대로
동작하는지 검토용으로 예외적으로 커밋한다. 이후 다른 구/전국 단위로 늘어나면 로컬 산출물은
다시 git 미추적(`data/*.jsonl` 규칙과 동일)으로 돌릴 것.
