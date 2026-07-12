// Flask 뷰어 API 공용 래퍼. 상대경로(api/...)라 각 뷰어 마운트(/profit/, /gangnam/ ...)에서
// 그대로 동작하고, dev(vite proxy)에서도 동일. 미로그인 시 API가 302 → HTML을 주므로
// 현재 경로를 next로 달아 로그인 페이지로 보낸다(뷰어 무관).

// 세션 동안 GET 응답을 메모리에 캐시 → 같은 요청 재클릭·재방문 시 즉시(네트워크 0).
// ttl=0 이면 캐시 안 함(예: 채팅 폴링처럼 항상 신선해야 하는 곳).
const _cache = new Map()   // path → { t, data }

export function clearCache() {
  _cache.clear()
}

export async function getJSON(path, { ttl = 60000 } = {}) {
  if (ttl > 0) {
    const hit = _cache.get(path)
    if (hit && Date.now() - hit.t < ttl) return hit.data
  }
  const res = await fetch(path, { credentials: 'same-origin' })
  const ct = res.headers.get('content-type') || ''
  if (!ct.includes('application/json')) {
    window.location.href = '/auth/login?next=' + encodeURIComponent(window.location.pathname)
    throw new Error('not authenticated')
  }
  if (!res.ok) throw new Error(`API ${path} → ${res.status}`)
  const data = await res.json()
  if (ttl > 0) _cache.set(path, { t: Date.now(), data })
  return data
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
  clearCache()   // 변경(POST/DELETE) 후엔 캐시 무효화 → 다음 GET은 신선하게
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
