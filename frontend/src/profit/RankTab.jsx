import { useState, useEffect, useCallback } from 'react'
import { fetchRank, fmt as n, fmtComp } from '../shared/api.js'

const RANK_COLS = [
  { k: 'rk', t: '#', s: false },
  { k: 'name', t: '이름', l: true },
  { k: 'comp', t: '경쟁' },
  { k: 'n', t: '매칭수' },
  { k: 'expNet', t: '기대월순수익' },
  { k: 'occ', t: '평균예약률%' },
  { k: 'net', t: '풀가동순수익' },
]
const medal = (i) => (i === 1 ? '🥇' : i === 2 ? '🥈' : i === 3 ? '🥉' : i)

export default function RankTab({ month }) {
  const [rooms, setRooms] = useState('')
  const [minMatch, setMinMatch] = useState('0')
  const [maxComp, setMaxComp] = useState('')
  const [data, setData] = useState(null)

  const load = useCallback(async () => {
    setData(await fetchRank({ rooms, month }))
  }, [rooms, month])

  useEffect(() => { load() /* eslint-disable-next-line */ }, [rooms, month])

  return (
    <div>
      <p className="legend" style={{ margin: '0 0 10px' }}>
        <b>기대 월순수익</b>(예약률 반영) 높은 순 = 실제로 가장 많이 남는 곳. <b>경쟁</b>=그 지역 삼삼 매물수 ·
        <b>매칭수</b>=부동산 매칭 표본 · 동은 <b>시군구</b>로 구분. 헤더 클릭 정렬.
      </p>
      <div className="panel" style={{ display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 10 }}>
        <div className="fg"><label>🛏️ 방 타입</label>
          <select value={rooms} onChange={(e) => setRooms(e.target.value)}>
            <option value="">전체</option><option>원룸</option><option>투룸</option><option>쓰리룸+</option>
          </select>
        </div>
        <div className="fg"><label>최소 매칭수 ≥</label><input type="number" value={minMatch} onChange={(e) => setMinMatch(e.target.value)} /></div>
        <div className="fg"><label>최대 경쟁 ≤</label><input type="number" value={maxComp} placeholder="비우면 전체" style={{ minWidth: 130 }} onChange={(e) => setMaxComp(e.target.value)} /></div>
      </div>
      <div className="rankwrap">
        <RankPanel title="🏘️ 동별 베스트" rows={data?.dong} minMatch={minMatch} maxComp={maxComp} />
        <RankPanel title="🚇 역별 베스트" rows={data?.station} minMatch={minMatch} maxComp={maxComp} />
      </div>
    </div>
  )
}

function RankPanel({ title, rows, minMatch, maxComp }) {
  const [kw, setKw] = useState('')
  const [sort, setSort] = useState({ col: 'expNet', dir: 'desc' })
  const list = rows || []

  const mm = parseInt(minMatch) || 0
  const mc = maxComp === '' ? null : parseFloat(maxComp)
  let filtered = list.filter((r) =>
    (r.n || 0) >= mm && (mc == null || (r.comp || 0) <= mc) && (!kw || (r.name || '').includes(kw)),
  )
  const pres = filtered.filter((r) => r[sort.col] != null)
  const mis = filtered.filter((r) => r[sort.col] == null)
  pres.sort((a, b) => {
    const av = a[sort.col], bv = b[sort.col]
    const c = typeof av === 'number' ? av - bv : String(av).localeCompare(String(bv), 'ko')
    return sort.dir === 'asc' ? c : -c
  })
  const data = [...pres, ...mis]

  const onSort = (k) => setSort((s) => (s.col === k ? { col: k, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { col: k, dir: 'desc' }))

  return (
    <div className="panel">
      <h3 style={{ margin: '2px 0 8px', fontSize: 14 }}>{title}
        <input placeholder="검색" value={kw} onChange={(e) => setKw(e.target.value)}
          style={{ fontSize: 12, padding: '4px 8px', border: '1px solid #d1d5db', borderRadius: 6, fontWeight: 400, marginLeft: 6 }} />
      </h3>
      <div style={{ overflow: 'auto', maxHeight: '68vh' }}>
        <table>
          <thead>
            <tr>
              {RANK_COLS.map((c) => {
                const sortable = c.s !== false
                return (
                  <th key={c.k} className={c.l ? 'l' : ''} style={sortable ? undefined : { cursor: 'default' }}
                    onClick={sortable ? () => onSort(c.k) : undefined}>
                    {c.t}{sortable && c.k === sort.col ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {data.length === 0 ? (
              <tr><td colSpan={RANK_COLS.length} className="d-empty">조건에 맞는 항목 없음</td></tr>
            ) : data.map((r, i) => {
              const zeroOcc = r.occ == null || r.occ === 0
              const ec = r.expNet == null ? '' : r.expNet >= 0 ? 'pos' : 'neg'
              return (
                <tr key={r.name}>
                  <td style={{ fontWeight: 800, fontSize: i < 3 ? 15 : 12 }}>{medal(i + 1)}</td>
                  <td className="l" style={{ fontWeight: 600 }}>{r.name}
                    {r.n != null && r.n <= 2 && <span title="매칭 표본 1~2건 — 순위·순수익 신뢰도 낮음" style={{ marginLeft: 5, fontSize: 10, fontWeight: 700, color: '#b45309', background: '#fef3c7', borderRadius: 4, padding: '1px 5px' }}>표본부족</span>}
                  </td>
                  <td title={r.comp >= 400 ? '삼삼 조회 상한(400)에 도달 — 실제는 더 많을 수 있음' : ''}>{fmtComp(r.comp)}</td>
                  <td style={r.n != null && r.n <= 2 ? { color: '#dc2626', fontWeight: 700 } : { color: '#94a3b8' }}>{n(r.n)}</td>
                  <td className={ec} style={{ fontWeight: 800, ...(ec === '' ? { color: '#94a3b8' } : {}) }}>{n(r.expNet)}</td>
                  <td className={zeroOcc ? '' : 'occ'} style={zeroOcc ? { color: '#dc2626', fontWeight: 700 } : undefined} title={zeroOcc ? '예약률 0% — 수요 미검증' : undefined}>{n(r.occ)}%</td>
                  <td style={{ color: '#94a3b8' }}>{n(r.net)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
