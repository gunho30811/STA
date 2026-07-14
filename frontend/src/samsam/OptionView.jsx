import { useState, useEffect, useCallback } from 'react'
import { getJSON } from '../shared/api.js'
import { buildParams, VERDICT, sgn, cmp } from './helpers.js'

const COLS = [
  { k: 'name', t: '옵션', l: true }, { k: 'verdict', t: '판정', l: true }, { k: 'adoption', t: '보유율%' },
  { k: 'have_n', t: '있는집' }, { k: 'none_n', t: '없는집' }, { k: 'diff', t: '단순차이' },
  { k: 'adj', t: '보정차이(같은평수·가격)' }, { k: 'adjn', t: '보정표본' }, { k: 'py', t: '평수(있/없)' }, { k: 'wk', t: '주당(있/없)' },
]
const LIST_COLS = [
  { k: 'name', t: '매물명 / 건물', l: true, s: false }, { k: 'sigungu', t: '지역', l: true }, { k: 'station', t: '역', l: true },
  { k: 'pyeong', t: '평' }, { k: 'week', t: '주당' }, { k: 'booked', t: '예약일' }, { k: 'blocked', t: '막힘' },
  { k: 'occ', t: '1달예약률%' }, { k: 'occ2', t: '2달%' }, { k: 'occ3', t: '3달%' }, { k: 'vac', t: '공실률%' },
  { k: 'options', t: '옵션', l: true, s: false }, { k: 'url', t: '링크', l: true, s: false },
]
const flat = (r) => ({
  option: r.option, name: r.name || r.option, verdict: r.verdict, adoption: r.adoption,
  have_n: r.have.n, none_n: r.none.n, diff: r.diff, adj: r.adj, adjn: r.adjn,
  have_py: r.have.pyeong, none_py: r.none.pyeong, have_wk: r.have.week, none_wk: r.none.week,
})
const sval = (x, k) => (k === 'py' ? x.have_py : k === 'wk' ? x.have_wk : x[k])
const LIST_NUM = new Set(['pyeong', 'week', 'booked', 'blocked', 'occ', 'occ2', 'occ3', 'vac'])

export default function OptionView({ filters, runSeq }) {
  const [overall, setOverall] = useState({})
  const [table, setTable] = useState([])
  const [sort, setSort] = useState({ col: null, dir: 'desc' })
  const [curOption, setCurOption] = useState('')
  const [curMode, setCurMode] = useState('none')
  const [list, setList] = useState(null)   // {items, total, oname} | null
  const [listSort, setListSort] = useState({ col: 'vac', dir: 'desc' })

  const analyze = useCallback(async () => {
    const r = await getJSON('api/analyze?' + buildParams(filters).toString())
    setOverall(r.overall || {})
    setTable(r.table || [])
    setCurOption(''); setList(null)
  }, [filters])

  useEffect(() => { analyze() /* eslint-disable-next-line */ }, [runSeq])

  const loadList = useCallback(async (option, mode) => {
    setCurOption(option); setCurMode(mode)
    const p = buildParams(filters); p.set('option', option); p.set('mode', mode)
    const r = await getJSON('api/listings?' + p.toString())
    setList({ items: r.items || [], total: r.total || 0, oname: r.optionName || option })
    setListSort({ col: 'vac', dir: 'desc' })
  }, [filters])

  // 옵션표 정렬(사용자가 헤더 클릭했을 때만; 아니면 서버 순서 유지)
  let rows = table.map(flat)
  if (sort.col) {
    const isNum = sort.col !== 'name' && sort.col !== 'verdict'
    const pres = rows.filter((x) => sval(x, sort.col) != null && sval(x, sort.col) !== '')
    const mis = rows.filter((x) => sval(x, sort.col) == null || sval(x, sort.col) === '')
    pres.sort((a, b) => cmp(sval(a, sort.col), sval(b, sort.col), isNum, sort.dir))
    rows = [...pres, ...mis]
  }
  const onSort = (k) => setSort((s) => (s.col === k ? { col: k, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { col: k, dir: 'desc' }))

  const listItems = list ? [...list.items].sort((a, b) => cmp(a[listSort.col], b[listSort.col], LIST_NUM.has(listSort.col), listSort.dir)) : []
  const onListSort = (k) => setListSort((s) => (s.col === k ? { col: k, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { col: k, dir: 'desc' }))

  return (
    <div>
      <div className="cards">
        <Card lbl="대상 매물수" val={(overall.n ?? 0).toLocaleString()} />
        <Card lbl="평균 예약률" val={<>{overall.occ ?? '-'}<small> %</small></>} />
        <Card lbl="평균 공실률" val={<>{overall.vac ?? '-'}<small> %</small></>} />
        <Card lbl="평균 주당임대료" val={<>{overall.week ?? '-'}<small> 만원</small></>} />
      </div>

      <div className="panel">
        <h2 className="sec">옵션별 예약률 영향 — 옵션을 클릭하면 그 옵션 <span className="badge2">없는 집</span> 목록이 아래에 떠요</h2>
        <p className="hint">
          <b>판정</b>: <span style={{ color: '#7c3aed', fontWeight: 700 }}>사실상 필수</span>=보유율 95%+ → 효과 측정 불가 ·
          <span style={{ color: '#4321F3', fontWeight: 700 }}> 측정가능</span>=보정차이 신뢰(크면 중요, 작거나 음수면 없어도 잘 나감) ·
          <span style={{ color: '#9ca3af', fontWeight: 700 }}> 표본부족</span>=비교 대상 적음. <b>단순차이</b>는 참고값.
        </p>
        <div className="scroll">
          <table>
            <thead><tr>{COLS.map((c) => (
              <th key={c.k} className={c.l ? 'l' : ''} onClick={() => onSort(c.k)}>
                {c.t}{c.k === sort.col ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
              </th>
            ))}</tr></thead>
            <tbody>
              {rows.map((x) => {
                const vd = VERDICT[x.verdict] || { t: x.verdict, c: '#666' }
                const meas = x.verdict === 'measurable'
                const acls = !meas ? 'mut' : x.adj == null ? 'mut' : x.adj <= 2 ? 'good' : 'bad'
                return (
                  <tr key={x.option} className={x.option === curOption ? 'on' : ''} onClick={() => loadList(x.option, 'none')}>
                    <td className="l"><span className="opt">{x.name}</span></td>
                    <td className="l"><span style={{ fontSize: 11, fontWeight: 800, color: '#fff', background: vd.c, padding: '2px 8px', borderRadius: 999 }}>{vd.t}</span></td>
                    <td>{x.adoption}%</td>
                    <td>{x.have_n}</td><td>{x.none_n}</td>
                    <td className="mut">{sgn(x.diff)}</td>
                    <td className={acls} style={{ fontWeight: 800 }}>{meas ? sgn(x.adj) : <span className="mut">{sgn(x.adj)}</span>}</td>
                    <td className="mut">{x.adjn || 0}</td>
                    <td className="mut">{x.have_py ?? '-'}/{x.none_py ?? '-'}</td>
                    <td className="mut">{x.have_wk ?? '-'}/{x.none_wk ?? '-'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {list && (
        <div className="panel">
          <div className="flex">
            <h2 className="sec">"{list.oname}" {curMode === 'none' ? '없는' : '있는'} 집 — {list.total.toLocaleString()}건 <span className="mut" style={{ fontSize: 12 }}>(헤더 클릭으로 정렬)</span></h2>
            <div className="toggle">
              <button className={curMode === 'none' ? 'on' : ''} onClick={() => loadList(curOption, 'none')}>옵션 없는 집</button>
              <button className={curMode === 'have' ? 'on' : ''} onClick={() => loadList(curOption, 'have')}>있는 집</button>
            </div>
          </div>
          <div className="scroll">
            <table>
              <thead><tr>{LIST_COLS.map((c) => {
                const sortable = c.s !== false
                return (
                  <th key={c.k} className={c.l ? 'l' : ''} style={sortable ? { cursor: 'pointer' } : { cursor: 'default' }} onClick={sortable ? () => onListSort(c.k) : undefined}>
                    {c.t}{sortable && c.k === listSort.col ? (listSort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                  </th>
                )
              })}</tr></thead>
              <tbody>
                {listItems.length === 0 ? (
                  <tr><td colSpan={LIST_COLS.length} className="empty">해당 매물이 없습니다.</td></tr>
                ) : listItems.map((x) => (
                  <tr key={x.room_id} style={{ cursor: 'default' }}>
                    <td className="l">{x.name}<br /><span className="mut">{x.building_name}</span></td>
                    <td className="l">{x.sigungu} {x.dong}</td><td className="l">{x.station || '-'}</td>
                    <td>{x.pyeong ?? '-'}</td><td>{x.week}</td><td>{x.booked}</td><td>{x.blocked}</td>
                    <td className="occ">{x.occ}%</td><td className="mut">{x.occ2}%</td><td className="mut">{x.occ3}%</td><td>{x.vac}%</td>
                    <td className="l" style={{ whiteSpace: 'normal', maxWidth: 280 }}>
                      {x.options.map((o, i) => <span className="pill" key={i}>{o}</span>)}
                      {curMode === 'none' && <span className="pill miss">{list.oname}</span>}
                    </td>
                    <td className="l"><a className="lnk" href={x.url} target="_blank" rel="noreferrer">렌트</a></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function Card({ lbl, val }) {
  return <div className="card"><div className="lbl">{lbl}</div><div className="val">{val}</div></div>
}
