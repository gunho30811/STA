import { useState, useEffect, useCallback } from 'react'
import { fetchProfit, fmt as n } from '../shared/api.js'
import Detail from './Detail.jsx'
import Pager from './Pager.jsx'

// 매물명 옆 시/도 컬럼 포함(기존 요청 반영). k=API 정렬키, l=좌측정렬.
const COLS = [
  { k: 'name', t: '매물명 / 건물', l: true },
  { k: 'sido', t: '시/도', l: true },
  { k: 'dong', t: '동', l: true },
  { k: 'station', t: '역', l: true },
  { k: 'pyeong', t: '평' },
  { k: 'expNet', t: '기대월순수익' },
  { k: 'occ', t: '예약률%' },
  { k: 'net', t: '순수익(풀가동)' },
  { k: 'maxRev', t: '최대수익' },
]

const DEFAULTS = {
  sido: '', sigungu: '', dong: '', btype: '', rooms: '',
  station: '', keyword: '', matches_min: '', net_min: '', maxrev_min: '',
  occ_min: '20', dongocc_min: '', dep_max: '', pyeong_min: '', pyeong_max: '',
}

export default function ProfitList({ facets, month }) {
  const [f, setF] = useState(DEFAULTS)
  const [sort, setSort] = useState({ col: 'expNet', dir: 'desc' })
  const [res, setRes] = useState({ items: [], summary: {}, total: 0, page: 1, pages: 1 })
  const [sel, setSel] = useState(null)

  const tree = facets.tree || {}
  const upd = (k, v) => setF((s) => ({ ...s, [k]: v }))

  const doSearch = useCallback(async (page, sortOverride) => {
    const srt = sortOverride || sort
    const data = await fetchProfit({
      ...f, month, sort: srt.col, dir: srt.dir, page, size: 40,
    })
    setRes(data)
  }, [f, month, sort])

  // 최초 + 기준월 변경 시 재조회
  useEffect(() => { doSearch(1) /* eslint-disable-next-line */ }, [month])

  const onSort = (k) => {
    const dir = sort.col === k ? (sort.dir === 'asc' ? 'desc' : 'asc') : 'desc'
    const next = { col: k, dir }
    setSort(next)
    doSearch(1, next)
  }

  // 새 조건으로 검색하면 이전에 클릭해 둔 매물 상세는 초기화(QA #3). 정렬·페이징은 유지.
  const runSearch = () => {
    setSel(null)
    doSearch(1)
  }

  const reset = () => {
    setF(DEFAULTS)
    setSort({ col: 'expNet', dir: 'desc' })
    setSel(null)
    // DEFAULTS로 즉시 조회
    fetchProfit({ ...DEFAULTS, month, sort: 'expNet', dir: 'desc', page: 1, size: 40 }).then(setRes)
  }

  // 시/도 → 시군구 → 동 종속 드롭다운
  const sigungus = f.sido && tree[f.sido] ? Object.keys(tree[f.sido]).sort() : []
  const dongs = f.sido && f.sigungu && tree[f.sido]?.[f.sigungu] ? tree[f.sido][f.sigungu] : []

  const s = res.summary || {}

  return (
    <div>
      <div className="cards">
        <Card lbl="매물 수" val={(s.count ?? 0).toLocaleString()} />
        <Card lbl="순수익 중앙값" val={<>{n(s.net_med)}<small> 만원</small></>} />
        <Card lbl="순수익 최대" val={<>{n(s.net_max)}<small> 만원</small></>} cls="pos" />
        <Card lbl="예약률 중앙값" val={<>{n(s.occ_med)}<small> %</small></>} cls="occ" />
      </div>

      <div className="panel">
        <div className="filters">
          <Sel label="시/도" value={f.sido} onChange={(v) => setF((st) => ({ ...st, sido: v, sigungu: '', dong: '' }))} opts={facets.sido || []} />
          <Sel label="시군구" value={f.sigungu} onChange={(v) => setF((st) => ({ ...st, sigungu: v, dong: '' }))} opts={sigungus} />
          <Sel label="동" value={f.dong} onChange={(v) => upd('dong', v)} opts={dongs} />
          <Sel label="건물유형" value={f.btype} onChange={(v) => upd('btype', v)} opts={facets.btype || []} />
          <Sel label="방수" value={f.rooms} onChange={(v) => upd('rooms', v)} opts={['원룸', '투룸', '쓰리룸+']} />
          <Txt label="🚇 역 검색" value={f.station} onChange={(v) => upd('station', v)} ph="예: 강남" onEnter={runSearch} />
          <Txt label="키워드" value={f.keyword} onChange={(v) => upd('keyword', v)} ph="매물/건물" onEnter={runSearch} />
          <Num label="매칭수 ≥" value={f.matches_min} onChange={(v) => upd('matches_min', v)} ph="개" />
          <Num label="순수익 ≥" value={f.net_min} onChange={(v) => upd('net_min', v)} ph="만원" />
          <Num label="최대수익 ≥" value={f.maxrev_min} onChange={(v) => upd('maxrev_min', v)} ph="만원" />
          <Num label="예약률 ≥ (기본 20%)" value={f.occ_min} onChange={(v) => upd('occ_min', v)} ph="%" />
          <Num label="동예약률 ≥" value={f.dongocc_min} onChange={(v) => upd('dongocc_min', v)} ph="%" />
          <Num label="보증금 ≤" value={f.dep_max} onChange={(v) => upd('dep_max', v)} ph="만원" />
          <div className="fg">
            <label>평수</label>
            <div style={{ display: 'flex', gap: 4 }}>
              <input type="number" value={f.pyeong_min} onChange={(e) => upd('pyeong_min', e.target.value)} placeholder="최소" style={{ width: 60 }} />
              <input type="number" value={f.pyeong_max} onChange={(e) => upd('pyeong_max', e.target.value)} placeholder="최대" style={{ width: 60 }} />
            </div>
          </div>
          <div className="fg"><label>&nbsp;</label><button className="btn btn-go" onClick={runSearch}>검색</button></div>
          <div className="fg"><label>&nbsp;</label><button className="btn btn-reset" onClick={reset}>초기화</button></div>
        </div>
        <div className="legend">
          <b>최대수익</b>=삼삼 풀가동 월매출(주당×4.345) · <b>순수익</b>=최대수익−부동산월총 ·
          <b>예약률</b>=1달 예약일/(30−막힘일) · <b>동예약률</b>=같은 동 평균 예약률 ·
          <b>인근역</b>=매물 <b>500m 반경</b> 내 지하철역(없으면 공란) — 매물명 속 '○○역'은 등록자 문구라 실제 거리와 다를 수 있어요
        </div>
        <div className="legend warn">
          ⚠️ <b>순수익·최대수익은 "풀가동(100% 예약) 가정" 이론값</b>이에요. 예약률이 0%인 매물도 커 보일 수 있어
          기본값으로 <b>예약률 20% 이상</b>만 보여줍니다. 전체를 보려면 <b>예약률 ≥</b> 칸을 비우고 검색하세요.
        </div>
      </div>

      <div className="md">
        <div className="listcol">
          <table>
            <thead>
              <tr>
                {COLS.map((c) => (
                  <th key={c.k} className={c.l ? 'l' : ''} onClick={() => onSort(c.k)}>
                    {c.t}{c.k === sort.col ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {res.items.length === 0 ? (
                <tr><td colSpan={COLS.length} className="d-empty">조건에 맞는 매물 없음</td></tr>
              ) : res.items.map((x) => {
                const ec = x.expNet != null && x.expNet >= 0 ? 'pos' : 'neg'
                const zeroOcc = x.occ == null || x.occ === 0
                return (
                  <tr key={x.id} className={x.id === sel?.id ? 'on' : ''} onClick={() => setSel(x)}>
                    <td className="l"><span className="name">{x.name || ''}</span><br /><span className="sub">{x.bldg || ''}</span></td>
                    <td className="l">{x.sido || ''}</td>
                    <td className="l">{x.dong || ''}</td>
                    <td className="l">{x.station || '-'}</td>
                    <td>{n(x.pyeong)}</td>
                    <td className={ec} style={{ fontWeight: 800 }}>{n(x.expNet)}</td>
                    <td className={zeroOcc ? '' : 'occ'} style={zeroOcc ? { color: '#dc2626', fontWeight: 700 } : undefined}>{n(x.occ)}%</td>
                    <td style={{ color: '#94a3b8' }}>{n(x.net)}</td>
                    <td style={{ color: '#94a3b8' }}>{n(x.maxRev)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <Detail item={sel} />
      </div>
      <Pager page={res.page} pages={res.pages} onGo={(p) => doSearch(p)} />
    </div>
  )
}

function Card({ lbl, val, cls }) {
  return <div className="card"><div className="lbl">{lbl}</div><div className={`val ${cls || ''}`}>{val}</div></div>
}
function Sel({ label, value, onChange, opts }) {
  return (
    <div className="fg"><label>{label}</label>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">전체</option>
        {opts.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  )
}
function Txt({ label, value, onChange, ph, onEnter }) {
  return (
    <div className="fg"><label>{label}</label>
      <input value={value} placeholder={ph} onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && onEnter) onEnter() }} />
    </div>
  )
}
function Num({ label, value, onChange, ph }) {
  return (
    <div className="fg"><label>{label}</label>
      <input type="number" value={value} placeholder={ph} onChange={(e) => onChange(e.target.value)} />
    </div>
  )
}
