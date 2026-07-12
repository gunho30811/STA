// Flask 뷰어 API 래퍼. 상대경로(api/...)를 쓰면 프로덕션(/profit/ 마운트)과
// dev(vite proxy /profit/api → :8000) 양쪽에서 동일하게 동작한다.
//
// 인증: 세션 쿠키 기반이라 credentials:'same-origin' 이면 충분. 미로그인 시 API가
// 302 → /auth/login 으로 리다이렉트하는데, fetch는 리다이렉트를 따라가 로그인 HTML을
// 받으므로 JSON 파싱이 실패한다. 그 경우 로그인 페이지로 보낸다.

async function getJSON(path) {
  const res = await fetch(path, { credentials: 'same-origin' })
  const ct = res.headers.get('content-type') || ''
  if (!ct.includes('application/json')) {
    // 로그인 안 됨(HTML 응답) → 로그인으로
    window.location.href = '/auth/login?next=/profit/'
    throw new Error('not authenticated')
  }
  if (!res.ok) throw new Error(`API ${path} → ${res.status}`)
  return res.json()
}

export const fetchFacets = () => getJSON('api/facets')

export function fetchProfit(params) {
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== '' && v != null) qs.set(k, v)
  })
  return getJSON('api/profit?' + qs.toString())
}

export function fetchRank(params) {
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => { if (v) qs.set(k, v) })
  return getJSON('api/rank?' + qs.toString())
}

export function fetchRecommend(params) {
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => { if (v !== '' && v != null) qs.set(k, v) })
  return getJSON('api/recommend?' + qs.toString())
}

// 숫자 포맷(기존 n() 헬퍼와 동일: null → '-', 아니면 천단위 콤마)
export function fmt(x) {
  return x == null ? '-' : x.toLocaleString()
}
