// Flask 뷰어 API 공용 래퍼. 상대경로(api/...)라 각 뷰어 마운트(/profit/, /gangnam/ ...)에서
// 그대로 동작하고, dev(vite proxy)에서도 동일. 미로그인 시 API가 302 → HTML을 주므로
// 현재 경로를 next로 달아 로그인 페이지로 보낸다(뷰어 무관).

export async function getJSON(path) {
  const res = await fetch(path, { credentials: 'same-origin' })
  const ct = res.headers.get('content-type') || ''
  if (!ct.includes('application/json')) {
    window.location.href = '/auth/login?next=' + encodeURIComponent(window.location.pathname)
    throw new Error('not authenticated')
  }
  if (!res.ok) throw new Error(`API ${path} → ${res.status}`)
  return res.json()
}

// 변경 요청(POST/DELETE 등). body 있으면 JSON. {ok,status,data} 반환.
export async function sendJSON(path, method = 'POST', body) {
  const res = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: body != null ? { 'Content-Type': 'application/json' } : undefined,
    body: body != null ? JSON.stringify(body) : undefined,
  })
  const ct = res.headers.get('content-type') || ''
  const data = ct.includes('application/json') ? await res.json() : null
  return { ok: res.ok, status: res.status, data }
}

// URLSearchParams 빌더: 빈 값 제외. multi에 담긴 키는 append(다중선택).
export function qs(params, multi = {}) {
  const p = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== '' && v != null) p.set(k, v)
  })
  Object.entries(multi).forEach(([k, arr]) => {
    (arr || []).forEach((v) => { if (v !== '' && v != null) p.append(k, v) })
  })
  return p
}

// ── profit 뷰어 전용 헬퍼 ──
export const fetchFacets = () => getJSON('api/facets')
export const fetchProfit = (params) => getJSON('api/profit?' + qs(params).toString())
export const fetchRank = (params) => getJSON('api/rank?' + qs(params).toString())
export const fetchRecommend = (params) => getJSON('api/recommend?' + qs(params).toString())

// 숫자 포맷(null → '-', 아니면 천단위 콤마)
export function fmt(x) {
  return x == null ? '-' : x.toLocaleString()
}
