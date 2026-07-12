import { useState, useEffect, useCallback } from 'react'
import { getJSON } from '../shared/api.js'
import { medal } from './helpers.js'

// 수도권 전체 리더보드 — 위 지역 필터 무시하고 자체 컨트롤만 사용(기존 동작 유지).
export default function RankingView() {
  const [sido, setSido] = useState('')
  const [minn, setMinn] = useState('2')
  const [by, setBy] = useState('occ_avg')
  const [top, setTop] = useState('100')
  const [rank, setRank] = useState([])

  const load = useCallback(async () => {
    const p = new URLSearchParams()
    p.set('min_n', minn)
    if (sido) p.set('sido', sido)
    const r = await getJSON('api/buildings?' + p.toString())
    setRank(r.items || [])
  }, [minn, sido])

  useEffect(() => { load() }, [load])

  const rows = [...rank].sort((a, b) => (b[by] ?? -1) - (a[by] ?? -1))
  const topN = parseInt(top, 10) || 0
  const shown = topN > 0 ? rows.slice(0, topN) : rows
  const occc = (v) => (v >= 80 ? 'good' : v < 40 ? 'bad' : 'occ')

  const cols = ['순위', '건물명', '지역', '유형', '평', '매물수', '1달%', '2달%', '3달%', '평균주당', '월순수익']

  return (
    <div className="panel">
      <h2 className="sec">🏆 건물 예약률 랭킹 — 수도권 전체 리더보드 (위 지역 필터 무시, 전 지역 1등부터)</h2>
      <p className="hint">같은 건물·평수로 묶은 <b>평균 예약률</b> 순위. <b>3달</b>로 정렬하면 <b>몇 달치가 이미 찬 검증된 핫 건물</b>이 위로 와요.</p>
      <div style={{ margin: '4px 0 12px', display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'center', fontSize: 12.5 }}>
        <span>시/도 <select value={sido} onChange={(e) => setSido(e.target.value)} style={selStyle}>
          <option value="">수도권 전체</option><option>서울특별시</option><option>경기도</option><option>인천광역시</option></select></span>
        <span>정렬 <select value={by} onChange={(e) => setBy(e.target.value)} style={selStyle}>
          <option value="occ_avg">1달 예약률</option><option value="occ2_avg">2달 예약률</option><option value="occ3_avg">3달 예약률</option></select></span>
        <span>최소 매물수 <select value={minn} onChange={(e) => setMinn(e.target.value)} style={selStyle}><option>2</option><option>1</option><option>3</option><option>5</option></select>채 이상</span>
        <span>표시 <select value={top} onChange={(e) => setTop(e.target.value)} style={selStyle}><option>100</option><option>50</option><option>200</option><option value="0">전체</option></select>위</span>
        <span className="mut">{rank.length ? `${rank.length.toLocaleString()}개 건물 중 ${shown.length}개 표시` : ''}</span>
      </div>
      <div className="scroll">
        <table>
          <thead><tr>{cols.map((t, i) => <th key={i} className={[1, 2, 3].includes(i) ? 'l' : ''}>{t}</th>)}</tr></thead>
          <tbody>
            {shown.length === 0 ? (
              <tr><td colSpan={11} className="empty">조건에 맞는 건물이 없습니다.</td></tr>
            ) : shown.map((r, i) => {
              const rk = i + 1
              return (
                <tr key={i}>
                  <td style={{ fontWeight: 800, fontSize: rk <= 3 ? 16 : 13 }}>{medal(rk)}</td>
                  <td className="l" style={{ fontWeight: 700 }}>{r.building}</td>
                  <td className="l">{r.sigungu} {r.dong}</td>
                  <td className="l">{r.btype}</td><td>{r.pyeong ?? '-'}</td>
                  <td style={{ fontWeight: 700 }}>{r.n}</td>
                  <td className={occc(r.occ_avg)} style={{ fontWeight: by === 'occ_avg' ? 800 : 400 }}>{r.occ_avg}%</td>
                  <td className={by === 'occ2_avg' ? occc(r.occ2_avg) : 'mut'} style={{ fontWeight: by === 'occ2_avg' ? 800 : 400 }}>{r.occ2_avg}%</td>
                  <td className={by === 'occ3_avg' ? occc(r.occ3_avg) : 'mut'} style={{ fontWeight: by === 'occ3_avg' ? 800 : 400 }}>{r.occ3_avg}%</td>
                  <td>{r.week_avg}</td>
                  <td>{r.net_avg == null ? <span className="mut">-</span> : <span className={r.net_avg >= 0 ? 'good' : 'bad'} style={{ fontWeight: 700 }}>{r.net_avg}</span>}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

const selStyle = { padding: '6px 8px', border: '1px solid #d1d5db', borderRadius: 6 }
