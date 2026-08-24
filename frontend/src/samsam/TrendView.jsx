import { useState, useEffect, useCallback } from 'react'
import { getJSON } from '../shared/api.js'
import { cmp } from './helpers.js'

// 지역(동) 예약률 트렌드 — 주간 스냅샷. 위 시/도·시군구 필터 적용. 날짜 컬럼은 동적.
export default function TrendView({ filters, runSeq }) {
  const [trd, setTrd] = useState({ dates: [], items: [] })
  const [minn, setMinn] = useState('5')
  const [sort, setSort] = useState({ col: 'occ1', dir: 'desc' })

  const load = useCallback(async () => {
    const p = new URLSearchParams()
    ;(filters.sidos || []).forEach((s) => p.append('sido', s))
    if (filters.sigungu) p.set('sigungu', filters.sigungu)
    if (filters.btype) p.set('building_type', filters.btype)   // 건물유형별 지역 예약률
    setTrd(await getJSON('api/trend?' + p.toString()))
  }, [filters])

  useEffect(() => { load() /* eslint-disable-next-line */ }, [runSeq])

  const baseCols = [
    { k: 'sido', t: '시도', l: true }, { k: 'sigungu', t: '시군구', l: true }, { k: 'dong', t: '동', l: true }, { k: 'n', t: '매물수' },
    { k: 'occ1', t: '1달%' }, { k: 'occ2', t: '2달%' }, { k: 'occ3', t: '3달%' }, { k: 'delta', t: '전주대비Δ' },
    { k: 'net', t: '평균순수익' }, { k: 'top', t: '최고 인기 오피스텔', l: true },
  ]
  const cols = [...baseCols, ...trd.dates.map((d) => ({ k: 'd:' + d, t: d.slice(5), date: true }))]

  const onSort = (k) => setSort((s) => (s.col === k ? { col: k, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { col: k, dir: 'desc' }))
  const val = (x, k) => (k.startsWith('d:') ? (x.series[k.slice(2)] ?? null) : x[k])
  const isNum = ['n', 'occ1', 'occ2', 'occ3', 'delta', 'net'].includes(sort.col) || sort.col.startsWith('d:')
  const minN = parseInt(minn) || 0
  const rows = trd.items.filter((x) => (x.n || 0) >= minN)
    .sort((a, b) => cmp(val(a, sort.col), val(b, sort.col), isNum, sort.dir))

  const occc = (v) => (v == null ? 'mut' : v >= 60 ? 'good' : v < 30 ? 'bad' : 'occ')

  return (
    <div className="panel">
      <h2 className="sec">지역(동) 예약률 트렌드 📈 — 매주 스냅샷으로 인기 오르는/내리는 동네 추적</h2>
      <p className="hint"><b>최신예약률</b>·<b>전주대비 Δ</b>(▲상승, ▼하락) + 주차별 값. 위 <b>시/도·시군구·건물유형</b> 필터 적용 → 유형별 지역 예약률.</p>
      <div style={{ margin: '4px 0 10px', display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'center', fontSize: 12.5 }}>
        <span>최소 매물수 <input type="number" value={minn} style={{ width: 64, padding: '6px 8px', border: '1px solid #d1d5db', borderRadius: 6 }} onChange={(e) => setMinn(e.target.value)} />개 이상</span>
        <span className="mut" style={{ fontSize: 11.5 }}>1달/2달/3달 예약률 · 평균순수익(보증금1000·렌트운영) · 예약률 20%↑ 동만 · 헤더 클릭 정렬</span>
      </div>
      {trd.items.length === 0 && (
        <div className="empty">아직 스냅샷이 없어요. 크롤 후 <code>python pipeline/samsam/snapshot.py</code> 실행하면 첫 데이터가 쌓여요.</div>
      )}
      <div className="scroll">
        <table>
          <thead><tr>{cols.map((col) => (
            <th key={col.k} className={col.l ? 'l' : ''} onClick={() => onSort(col.k)}>
              {col.t}{col.k === sort.col ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
            </th>
          ))}</tr></thead>
          <tbody>
            {trd.items.length > 0 && rows.length === 0 ? (
              <tr><td colSpan={cols.length} className="empty">조건(예약률 20%↑·매물수 {minN}↑)에 맞는 동이 없어요.</td></tr>
            ) : rows.map((x, i) => {
              const dcell = x.delta == null ? <span className="mut">-</span>
                : x.delta > 0 ? <span className="bad">▲{x.delta}</span>
                : x.delta < 0 ? <span style={{ color: '#1B72E8', fontWeight: 700 }}>▼{Math.abs(x.delta)}</span> : '0'
              const to = x.top_office
              return (
                <tr key={i}>
                  <td className="l mut">{x.sido || '-'}</td><td className="l">{x.sigungu}</td><td className="l" style={{ fontWeight: 700 }}>{x.dong}</td>
                  <td>{x.n}</td>
                  <td className={occc(x.occ1)} style={{ fontWeight: 800 }}>{x.occ1 ?? '-'}%</td>
                  <td className={occc(x.occ2)}>{x.occ2 ?? '-'}%</td>
                  <td className={occc(x.occ3)}>{x.occ3 ?? '-'}%</td>
                  <td>{dcell}</td>
                  <td>{x.net == null ? <span className="mut">-</span> : <b style={{ color: x.net >= 0 ? '#059669' : '#dc2626' }}>{x.net >= 0 ? '+' : ''}{x.net.toLocaleString()}만</b>}</td>
                  <td className="l">{to ? <a href={to.url} target="_blank" rel="noreferrer" className="lnk" style={{ background: '#0369a1' }}>{to.name} · {to.occ}%</a> : <span className="mut">-</span>}</td>
                  {trd.dates.map((d) => <td key={d} className="mut">{x.series[d] ?? '-'}</td>)}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
