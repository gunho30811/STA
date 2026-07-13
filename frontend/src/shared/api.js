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

// ── localStorage 영속 캐시(stale-while-revalidate) ──
// 첫 방문 결과를 브라우저에 저장 → 재방문/새로고침 때 네트워크 대기 없이 즉시 표시하고
// 뒤에서 조용히 최신으로 갱신. 매물처럼 하루 1회 갱신되는 데이터에 적합.
const LS_PREFIX = 'sta:'
const LS_TTL = 24 * 60 * 60 * 1000   // 24시간(그 이상 오래된 캐시는 안 씀)

export function readCache(path) {
  try {
    const raw = localStorage.getItem(LS_PREFIX + path)
    if (!raw) return null
    const { t, data } = JSON.parse(raw)
    if (Date.now() - t > LS_TTL) return null
    return data
  } catch { return null }
}

export function writeCache(path, data) {
  try {
    localStorage.setItem(LS_PREFIX + path, JSON.stringify({ t: Date.now(), data }))
  } catch { /* 용량 초과 등은 무시 */ }
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

// 경쟁(동삼삼매물수) 표시. 삼삼 API 조회 상한 400에서 잘리므로 400은 '400+'로 표기.
export function fmtComp(x) {
  if (x == null) return '-'
  return x >= 400 ? '400+' : x.toLocaleString()
}

// 시/도 축약(서울특별시→서울). 좁은 화면에서 지역을 짧게 보여줄 때.
const _SIDO_SHORT = { 서울특별시: '서울', 경기도: '경기', 인천광역시: '인천' }
export function shortSido(s) {
  return _SIDO_SHORT[s] || s || ''
}
