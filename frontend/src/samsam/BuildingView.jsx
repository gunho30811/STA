import { useState, useEffect, useCallback } from 'react'
import { getJSON } from '../shared/api.js'
import { buildParams, csvCell, cmp } from './helpers.js'

const BCOLS = [
  { k: 'building', t: '건물명', l: true }, { k: 'dong', t: '동', l: true }, { k: 'btype', t: '유형', l: true },
  { k: 'pyeong', t: '평' }, { k: 'station', t: '역', l: true }, { k: 'n', t: '매물수' }, { k: 'occ_avg', t: '1달예약률%' },
  { k: 'occ2_avg', t: '2달%' }, { k: 'occ3_avg', t: '3달%' },
  { k: 'occ_min', t: '최저(1달)%' }, { k: 'occ_max', t: '최고(1달)%' }, { k: 'week_avg', t: '평균주당' },
  { k: 'net_avg', t: '월순수익' }, { k: 'breakeven', t: '손익분기(주)' }, { k: 'n_matched', t: '매칭수' },
  { k: 'links', t: '링크', l: true, s: false },
]
const NUM = new Set(['pyeong', 'n', 'occ_avg', 'occ2_avg', 'occ3_avg', 'occ_min', 'occ_max', 'week_avg', 'net_avg', 'breakeven', 'n_matched'])

export default function BuildingView({ filters, runSeq }) {
  const [ctrl, setCtrl] = useState({ building: '', station: '', minn: '2', deposit: '1000', fixed: '0', occmin: '', netmin: '', bemax: '' })
  const [bld, setBld] = useState([])
  const [sort, setSort] = useState({ col: 'occ_avg', dir: 'desc' })
  const [modal, setModal] = useState(null)   // 모바일: 행 탭 시 전체 상세 모달
  const c = (k, v) => setCtrl((s) => ({ ...s, [k]: v }))

  const load = useCallback(async () => {
    const p = buildParams(filters)
    p.set('min_n', ctrl.minn)
    if (ctrl.deposit) p.set('deposit', ctrl.deposit)
    if (ctrl.fixed) p.set('fixed', ctrl.fixed)
    if (ctrl.station.trim()) p.set('station', ctrl.station.trim())
    if (ctrl.occmin.trim()) p.set('occ_min_filter', ctrl.occmin.trim())
    if (ctrl.netmin.trim()) p.set('net_min_filter', ctrl.netmin.trim())
    if (ctrl.bemax.trim()) p.set('breakeven_max', ctrl.bemax.trim())
    if (ctrl.building.trim()) p.set('building', ctrl.building.trim())
    const r = await getJSON('api/buildings?' + p.toString())
    setBld(r.items || [])
  }, [filters, ctrl])

  useEffect(() => { load() /* eslint-disable-next-line */ }, [runSeq])

  const onSort = (k) => setSort((s) => (s.col === k ? { col: k, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { col: k, dir: 'desc' }))

  // 손익분기점은 '작을수록 좋음' — null은 항상 뒤로.
  const rows = [...bld].sort((a, b) => {
    const k = sort.col
    if (NUM.has(k)) {
      const far = sort.dir === 'asc' ? Infinity : -Infinity
      const av = a[k] == null ? far : a[k], bv = b[k] == null ? far : b[k]
      return sort.dir === 'asc' ? av - bv : bv - av
    }
    return cmp(a[k], b[k], false, sort.dir)
  })

  const downloadCsv = () => {
    if (!rows.length) { alert('다운로드할 건물이 없습니다.'); return }
    const header = ['건물명', '시군구', '동', '유형', '평', '역', '매물수', '1달예약률%', '2달예약률%', '3달예약률%', '최저예약률%(1달)', '최고예약률%(1달)',
      '평균주당(만원)', '월순수익(만원)', '손익분기점(주)', '매칭수', '부동산번호', '중개사무소', '대표월세(만원)', '대표보증금(만원)', '대표층수', '부동산링크', '렌트_경쟁예시링크']
    const lines = [header.map(csvCell).join(',')]
    rows.forEach((r) => lines.push([r.building, r.sigungu, r.dong, r.btype, r.pyeong ?? '', r.station || '', r.n, r.occ_avg,
      r.occ2_avg, r.occ3_avg, r.occ_min, r.occ_max, r.week_avg, r.net_avg ?? '', r.breakeven ?? '', r.n_matched || 0,
      r.phone || '', r.office || '', r.nv_rent ?? '', r.nv_dep ?? '', r.nv_floor || '', r.naver_url || '', r.sam_url || ''].map(csvCell).join(',')))
    const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `렌트_건물인기_${new Date().toISOString().slice(0, 10)}.csv`
    document.body.appendChild(a); a.click(); a.remove()
  }

  return (
    <div className="panel">
      <h2 className="sec">건물(오피스텔) 인기 순위 — 한 건물에 렌트 여러 채가 <b>다 잘 나가면</b> 검증된 건물 🔥</h2>
      <p className="hint">
        <b>매물수</b>=그 건물의 렌트 매물 수. <b>최저예약률</b>이 높으면 <b>전 호실이 다 잘 나간다</b>는 뜻. 위 필터 적용됨.{' '}
        <b>건물명 검색</b> <input value={ctrl.building} placeholder="예: 롯데캐슬" style={{ width: 110 }} onChange={(e) => c('building', e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') load() }} /> ·{' '}
        <b>역 검색</b> <input value={ctrl.station} placeholder="예: 강남역" style={{ width: 100 }} onChange={(e) => c('station', e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') load() }} />{' '}
        <button className="btn btn-go" style={{ padding: '4px 10px', fontSize: 12 }} onClick={load}>검색</button> ·{' '}
        최소 매물수 <select value={ctrl.minn} onChange={(e) => { c('minn', e.target.value) }}><option>2</option><option>1</option><option>3</option><option>5</option></select>채 이상 ·{' '}
        <b>보증금</b> <input type="number" value={ctrl.deposit} style={{ width: 80 }} onChange={(e) => c('deposit', e.target.value)} /> 만 ·{' '}
        <b>고정비</b> <input type="number" value={ctrl.fixed} style={{ width: 70 }} onChange={(e) => c('fixed', e.target.value)} /> 만 빼기 ·{' '}
        <b>최저예약률</b> <input type="number" value={ctrl.occmin} placeholder="예: 50" style={{ width: 60 }} onChange={(e) => c('occmin', e.target.value)} /> % 이상 ·{' '}
        <b>월순수익</b> <input type="number" value={ctrl.netmin} placeholder="예: 0" style={{ width: 60 }} onChange={(e) => c('netmin', e.target.value)} /> 만원 이상 ·{' '}
        <b>손익분기점</b> <input type="number" value={ctrl.bemax} placeholder="예: 3" style={{ width: 55 }} onChange={(e) => c('bemax', e.target.value)} /> 주 이하 ·<br />
        월순수익=렌트월매출−부동산월세@보증금−관리비−고정비 (셀에 마우스 올리면 분해) · <b>손익분기점(주)</b>=작을수록 회수 빠름 · 헤더 클릭 정렬.
      </p>
      <div className="flex">
        <span className="mut" style={{ fontSize: 12 }}>{rows.length ? `${rows.length.toLocaleString()}개 건물` : ''}</span>
        <button className="btn btn-go" style={{ padding: '6px 14px', fontSize: 12 }} onClick={downloadCsv}>📥 CSV 다운로드</button>
      </div>

      {/* PC: 전체 표 (행 클릭 시에도 상세 모달) */}
      <div className="scroll desk-only">
        <table>
          <thead><tr>{BCOLS.map((col) => {
            const sortable = col.s !== false
            return (
              <th key={col.k} className={col.l ? 'l' : ''} style={sortable ? undefined : { cursor: 'default' }} onClick={sortable ? () => onSort(col.k) : undefined}>
                {col.t}{sortable && col.k === sort.col ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
              </th>
            )
          })}</tr></thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={BCOLS.length} className="empty">매물수 조건을 만족하는 건물이 없습니다.</td></tr>
            ) : rows.map((r, i) => <BuildingRow key={i} r={r} onClick={() => setModal(r)} />)}
          </tbody>
        </table>
      </div>

      {/* 모바일: 핵심(건물명·시군구·동·예약률)만 카드로, 탭하면 전체 상세 모달 */}
      <div className="mob-only mob-list">
        {rows.length === 0 ? (
          <div className="empty">매물수 조건을 만족하는 건물이 없습니다.</div>
        ) : rows.map((r, i) => {
          const occc = r.occ_avg >= 60 ? 'good' : r.occ_avg < 30 ? 'bad' : 'occ'
          return (
            <button className="mob-item" key={i} onClick={() => setModal(r)}>
              <div className="mi-top">
                <span className="mi-name">{r.building}</span>
                <span className={`mi-occ ${occc}`}>{r.occ_avg}%</span>
              </div>
              <div className="mi-sub">
                📍 {[r.sigungu, r.dong].filter(Boolean).join(' ') || '-'}
                <span className="mut"> · {r.btype} · {r.pyeong ?? '-'}평 · 매물 {r.n}</span>
              </div>
            </button>
          )
        })}
      </div>

      {modal && <BuildingModal r={modal} onClose={() => setModal(null)} />}
    </div>
  )
}

function BuildingRow({ r, onClick }) {
  const minc = r.occ_min >= 60 ? 'good' : r.occ_min < 30 ? 'bad' : 'mut'
  const netc = r.net_avg == null ? 'mut' : r.net_avg >= 0 ? 'good' : 'bad'
  let netTip = ''
  if (r.bd) { const b = r.bd; netTip = `렌트 월매출 ${b.maxRev}\n− 부동산월세(보증금 ${b.dep}만 기준) ${b.rent}\n− 관리비 ${b.mgmt}` + (b.fixed ? `\n− 고정비 ${b.fixed}` : '') + `\n= 월순수익 ${r.net_avg} 만원` }
  const bc = r.breakeven == null ? '' : r.breakeven <= 3 ? 'good' : r.breakeven > 6 ? 'bad' : ''
  let beTip = ''
  if (r.bd && r.breakeven != null) { const b = r.bd; beTip = `(부동산월세 ${b.rent} + 관리비 ${b.mgmt}) ÷ 주당 ${r.week_avg}\n= ${r.breakeven}주면 월 고정비용 회수` }
  const stop = (e) => e.stopPropagation()   // 링크 클릭이 행 모달을 열지 않게
  return (
    <tr onClick={onClick}>
      <td className="l" style={{ fontWeight: 700 }}>{r.building}</td>
      <td className="l">{r.dong}</td><td className="l">{r.btype}</td><td>{r.pyeong ?? '-'}</td><td className="l">{r.station || '-'}</td>
      <td style={{ fontWeight: 800 }}>{r.n}</td><td className="occ">{r.occ_avg}%</td>
      <td className="mut">{r.occ2_avg}%</td><td className="mut">{r.occ3_avg}%</td>
      <td className={minc} style={{ fontWeight: 800 }}>{r.occ_min}%</td><td className="mut">{r.occ_max}%</td><td>{r.week_avg}</td>
      <td>{r.net_avg == null ? <span className="mut">-</span>
        : <span className={netc} style={{ fontWeight: 800, cursor: 'help', borderBottom: '1px dotted #94a3b8' }} title={netTip}>{r.net_avg}</span>}</td>
      <td>{r.breakeven == null ? <span className="mut">-</span>
        : <span className={bc} style={{ fontWeight: 800, cursor: 'help', borderBottom: '1px dotted #94a3b8' }} title={beTip}>{r.breakeven}주</span>}</td>
      <td className="mut">{r.n_matched || 0}</td>
      <td className="l" onClick={stop}>
        {r.sam_url ? <a className="lnk" href={r.sam_url} target="_blank" rel="noreferrer">렌트 예시</a> : null}
        {r.naver_url ? <> <a className="lnk" style={{ background: '#4321F3' }} href={r.naver_url} target="_blank" rel="noreferrer">부동산</a></> : null}
        {!r.sam_url && !r.naver_url ? <span className="mut">-</span> : null}
      </td>
    </tr>
  )
}

// 모바일 상세 모달 — 표에 있던 전 항목을 세로로.
function BuildingModal({ r, onClose }) {
  const Row = ({ label, children }) => (
    <div className="md-row"><span className="md-k">{label}</span><span className="md-v">{children}</span></div>
  )
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            <div className="modal-title">{r.building}</div>
            <div className="modal-sub">📍 {[r.sigungu, r.dong].filter(Boolean).join(' ') || '-'} · {r.btype}</div>
          </div>
          <button className="modal-x" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">
          <Row label="1달 예약률"><b className="occ">{r.occ_avg}%</b> <span className="mut">(2달 {r.occ2_avg}% · 3달 {r.occ3_avg}%)</span></Row>
          <Row label="최저~최고(1달)">{r.occ_min}% ~ {r.occ_max}%</Row>
          <Row label="평 / 역">{r.pyeong ?? '-'}평 · {r.station || '-'}</Row>
          <Row label="매물수 / 매칭수">{r.n} 채 · {r.n_matched || 0}</Row>
          <Row label="평균 주당">{r.week_avg} 만원</Row>
          <Row label="월순수익">{r.net_avg == null ? '-' : <b className={r.net_avg >= 0 ? 'good' : 'bad'}>{r.net_avg} 만원</b>}</Row>
          <Row label="손익분기점">{r.breakeven == null ? '-' : `${r.breakeven} 주`}</Row>
          {r.bd && <Row label="수익 분해"><span className="mut" style={{ fontSize: 12 }}>렌트매출 {r.bd.maxRev} − 부동산월세 {r.bd.rent} − 관리비 {r.bd.mgmt}{r.bd.fixed ? ` − 고정비 ${r.bd.fixed}` : ''}</span></Row>}
          {(r.phone || r.office) && <Row label="부동산">{r.office || ''} {r.phone ? <a href={`tel:${r.phone}`}>{r.phone}</a> : ''}</Row>}
        </div>
        <div className="modal-foot">
          {r.sam_url ? <a className="lnk" href={r.sam_url} target="_blank" rel="noreferrer">렌트 예시</a> : null}
          {r.naver_url ? <a className="lnk" style={{ background: '#4321F3' }} href={r.naver_url} target="_blank" rel="noreferrer">부동산 매물</a> : null}
        </div>
      </div>
    </div>
  )
}
