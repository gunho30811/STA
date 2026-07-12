// samsam 뷰어 공용 헬퍼/상수 (기존 samsam.html 이식).

// 공용 필터(시도 복수·시군구·동·건물유형·평수·주당) → URLSearchParams
export function buildParams(f) {
  const p = new URLSearchParams()
  ;(f.sidos || []).forEach((s) => p.append('sido', s))
  if (f.sigungu) p.set('sigungu', f.sigungu)
  if (f.dong) p.set('dong', f.dong)
  if (f.btype) p.set('building_type', f.btype)
  if (f.pmin) p.set('pyeong_min', f.pmin)
  if (f.pmax) p.set('pyeong_max', f.pmax)
  if (f.wmin) p.set('week_min', f.wmin)
  if (f.wmax) p.set('week_max', f.wmax)
  return p
}

export const medal = (r) => (r === 1 ? '🥇' : r === 2 ? '🥈' : r === 3 ? '🥉' : r)
export const sgn = (v) => (v == null ? '-' : (v > 0 ? '+' : '') + v)

export const VERDICT = {
  measurable: { t: '측정가능', c: '#2563eb' },
  essential: { t: '사실상 필수', c: '#7c3aed' },
  lowsample: { t: '표본부족', c: '#9ca3af' },
}

// CSV 셀 이스케이프
export function csvCell(v) {
  const s = (v == null ? '' : String(v)).replace(/"/g, '""')
  return /[",\n]/.test(s) ? `"${s}"` : s
}

// 헤더 클릭 정렬 훅 로직: 숫자/문자 공통 비교기
export function cmp(av, bv, isNum, dir) {
  if (isNum) {
    av = av == null ? -Infinity : av
    bv = bv == null ? -Infinity : bv
    return dir === 'asc' ? av - bv : bv - av
  }
  const c = String(av == null ? '' : av).localeCompare(String(bv == null ? '' : bv), 'ko')
  return dir === 'asc' ? c : -c
}
