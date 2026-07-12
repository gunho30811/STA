// gangnam 뷰어 공용 상수/포맷 (기존 gangnam.html 이식).
export const TYPES = { APT: '아파트', OPST: '오피스텔', VL: '빌라', OR: '원룸', DDDGG: '단독/다가구', SG: '상가' }

// 상세 모달 필드 그룹 (있는 값만 표시). [key, label, unit?]
export const GROUPS = [
  ['가격', [['deposit', '보증금', '만원'], ['rent_monthly', '월세', '만원'], ['maintenance_monthly', '관리비', '만원'], ['maintenance_type', '관리비유형']]],
  ['면적·구조', [['pyeong', '전용', '평'], ['area_exclusive_m2', '전용', '㎡'], ['area_contract_m2', '계약', '㎡'], ['exclusive_ratio', '전용률', '%'], ['rooms', '방', '개'], ['bathrooms', '욕실', '개'], ['floor_current', '현재층'], ['floor_total', '총층'], ['direction', '향'], ['duplex', '복층'], ['entrance_type', '현관구조'], ['heating', '난방']]],
  ['위치', [['jibun_address', '지번주소'], ['road_address', '도로명'], ['dong', '동'], ['bldg_dong', '동/관'], ['lat', '위도'], ['lng', '경도']]],
  ['지하철', [['subway_station', '최근접역'], ['subway_distance_m', '거리', 'm'], ['subway_walk_min', '도보', '분'], ['subway_500m', '500m내'], ['subway_1km', '1km내']]],
  ['건물·단지', [['building_name', '건물명'], ['building_type', '유형'], ['building_use', '용도'], ['approval_date', '사용승인'], ['building_age', '연식', '년'], ['households', '세대수'], ['households_same_area', '동일면적세대'], ['dong_count', '동수'], ['parking_total', '총주차'], ['parking_per_household', '세대당주차'], ['floor_area_ratio', '용적률', '%'], ['building_coverage_ratio', '건폐율', '%'], ['builder', '건설사'], ['same_building_same_area_count', '동일건물동일면적매물']]],
  ['학교', [['school_name', '배정초교'], ['school_type', '구분'], ['school_walk_min', '도보', '분'], ['school_student_per_teacher', '교사1인당학생']]],
  ['중개사', [['agent_name', '중개사'], ['agent_office', '사무소'], ['agent_phone', '연락처'], ['agent_reg_no', '등록번호'], ['agent_address', '주소'], ['agent_owner_confirmed_3m', '3개월집주인확인']]],
  ['기타', [['broker_fee_max', '중개보수상한', '만원'], ['broker_fee_rate', '요율', '%'], ['move_in', '입주'], ['confirmed_at', '확인일'], ['posted_at', '등록일'], ['tags', '태그'], ['facilities', '시설']]],
]

export function won(v) {
  return v == null ? '-' : Number(v).toLocaleString()
}

// jsonl엔 list가 문자열("[...]")로 저장된 경우가 있음
export function jarr(v) {
  if (Array.isArray(v)) return v
  if (typeof v === 'string' && v.startsWith('[')) {
    try { return JSON.parse(v) } catch { /* noop */ }
  }
  return v
}

// 상세값 표시 포맷: null/빈값 → null, 배열/불리언/숫자/문자 처리
export function fmtVal(v) {
  v = jarr(v)
  if (v == null || v === '') return null
  if (Array.isArray(v)) return v.length ? v.join(', ') : null
  if (v === true) return '예'
  if (v === false) return '아니오'
  if (typeof v === 'number') return v.toLocaleString()
  return String(v)
}
