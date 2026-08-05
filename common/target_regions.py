# -*- coding: utf-8 -*-
"""크롤·노출 대상 지역을 한 곳에서 관리한다.

기본은 수도권(서울·경기·인천) 전량. 수도권 밖은 **매일 크롤로 갱신하는 지역만** 대상에
넣는다 — 갱신이 안 되는 지역은 예약률이 몇 주씩 동결돼 화면에 섞이면 오히려 해가 되기
때문(2026-07-30 결정, 데이터 자체는 DB에 보존).

주의: 시도 표기가 소스마다 다르다. 삼삼은 '서울특별시/인천광역시/부산광역시', 네이버는
'서울시/인천시/부산시'. 그래서 시도는 **접두**로, 추가 지역은 '시도 시군구' 문자열의
**부분일치**로 매칭한다.

EXTRA_REGIONS는 콤마구분 토큰 목록이며 env RENDIT_EXTRA_REGIONS로 덮어쓴다(빈 값 = 수도권만).
  '부산'   → '부산광역시 …' · '부산시 …' 둘 다
  '천안시' → '충청남도 천안시 서북구' · '…동남구' 둘 다
"""
import os

METRO_SIDO = {'서울특별시', '경기도', '인천광역시'}   # 삼삼 표기(기존 코드 호환용)
METRO_PREFIX = ('서울', '경기', '인천')               # 소스별 표기차 흡수
_DEFAULT_EXTRA = '부산,천안시'                        # 2026-08-05 추가 — 일일 크롤 대상
EXTRA_REGIONS = [s.strip() for s in
                 os.environ.get('RENDIT_EXTRA_REGIONS', _DEFAULT_EXTRA).split(',') if s.strip()]


def in_target(sido, sigungu=''):
    """(시도, 시군구)가 크롤·노출 대상인가."""
    if (sido or '').startswith(METRO_PREFIX):
        return True
    label = f"{sido or ''} {sigungu or ''}"
    return any(t in label for t in EXTRA_REGIONS)


def sql_where(sido_col='sido', sigungu_col='sigungu'):
    """대상 지역 SQL 조건 → (WHERE 조각, 파라미터 리스트). 플레이스홀더는 %s."""
    parts, params = [], []
    for p in METRO_PREFIX:
        parts.append(f"{sido_col} LIKE %s")
        params.append(p + '%')
    for t in EXTRA_REGIONS:
        parts.append(f"(coalesce({sido_col},'') || ' ' || coalesce({sigungu_col},'')) LIKE %s")
        params.append('%' + t + '%')
    return "(" + " OR ".join(parts) + ")", params


def sql_literal(sido_col='sido', sigungu_col='sigungu'):
    """sql_where()와 같은 조건을 파라미터 없이 리터럴로 — 뷰(CREATE VIEW) 정의용.
    값이 코드 상수뿐이라 주입 위험은 없지만 따옴표는 이스케이프한다."""
    where, params = sql_where(sido_col, sigungu_col)
    for p in params:
        where = where.replace('%s', "'" + p.replace("'", "''") + "'", 1)
    return where


_SHORT = {'서울': '서울', '경기': '경기', '인천': '인천', '부산': '부산', '대구': '대구',
          '광주': '광주', '대전': '대전', '울산': '울산', '세종': '세종', '강원': '강원',
          '충청북': '충북', '충청남': '충남', '충북': '충북', '충남': '충남',
          '전라북': '전북', '전라남': '전남', '전북': '전북', '전남': '전남',
          '경상북': '경북', '경상남': '경남', '경북': '경북', '경남': '경남', '제주': '제주'}


def short(sido):
    """표기가 제각각인 시도명 → 2글자 축약('서울특별시'·'서울시' → '서울')."""
    s = (sido or '').strip()
    for k, v in _SHORT.items():
        if s.startswith(k):
            return v
    return s


def label():
    """로그용 요약."""
    return "수도권(서울/경기/인천)" + (" + " + ", ".join(EXTRA_REGIONS) if EXTRA_REGIONS else "")
