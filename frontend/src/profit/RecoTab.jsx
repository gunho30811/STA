import { useState, useEffect } from 'react'
import { fetchRecommend, fmt as n, fmtComp } from '../shared/api.js'

const RECO_COLS = ['#', '이름', '경쟁', '매칭', '예약률%', '기대월순수익', '기회점수']
const medal = (i) => (i === 1 ? '🥇' : i === 2 ? '🥈' : i === 3 ? '🥉' : i)

export default function RecoTab({ month }) {
  const [occ, setOcc] = useState('30')
  const [comp, setComp] = useState('')
  const [minN, setMinN] = useState('2')
  const [rooms, setRooms] = useState('')
  const [data, setData] = useState(null)

  const load = async () => {
    setData(await fetchRecommend({ min_occ: occ, max_comp: comp, min_n: minN, rooms, month }))
  }
  useEffect(() => { load() /* eslint-disable-next-line */ }, [month])

  const d = data || { dong: [], station: [], office: [] }

  return (
    <div className="reco">
      <p className="legend hide-mobile" style={{ margin: '0 0 10px' }}>
        🎯 <b>신규진입 추천(블루오션)</b> — 수요 있고 <b>기대 월순수익</b> 좋은데 <b>경쟁 렌트 매물이 적은</b> 동/역/오피스텔.
        <b>기회점수</b> = 기대월순수익 ÷ √경쟁수.
      </p>
      <div className="panel" style={{ display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 10 }}>
        <div className="fg"><label>최소 예약률 ≥ (수요)</label><input type="number" value={occ} onChange={(e) => setOcc(e.target.value)} /></div>
        <div className="fg"><label>최대 경쟁 ≤ (렌트 매물수)</label><input type="number" value={comp} placeholder="비우면 전체" onChange={(e) => setComp(e.target.value)} /></div>
        <div className="fg"><label>최소 표본(매칭수) ≥</label><input type="number" value={minN} onChange={(e) => setMinN(e.target.value)} /></div>
        <div className="fg"><label>🛏️ 방 타입</label>
          <select value={rooms} onChange={(e) => setRooms(e.target.value)}>
            <option value="">전체</option><option>원룸</option><option>투룸</option><option>쓰리룸+</option>
          </select>
        </div>
        <div className="fg"><label>&nbsp;</label><button className="btn btn-go" onClick={load}>추천 보기</button></div>
        <span className="legend" style={{ margin: 0 }}>추천 동 {d.dong.length} · 역 {d.station.length} · 오피스텔 {d.office.length}</span>
      </div>
      <div className="rankwrap">
        <RecoArea title="🏘️ 추천 동" rows={d.dong} />
        <RecoArea title="🚇 추천 역" rows={d.station} />
      </div>
      <div className="panel">
        <h3 style={{ margin: '2px 0 8px', fontSize: 14 }}>🏢 추천 오피스텔 (개별 매물)</h3>
        <div style={{ overflow: 'auto', maxHeight: '60vh' }}>
          <table>
            <thead>
              <tr>{['#', '매물명', '동', '역', '평', '경쟁', '매칭', '예약률%', '기대월순수익', '기회점수', '링크'].map((t, i) =>
                <th key={i} className={[1, 2, 3, 10].includes(i) ? 'l' : ''}>{t}</th>)}</tr>
            </thead>
            <tbody>
              {d.office.length === 0 ? (
                <tr><td colSpan={11} className="d-empty">조건에 맞는 오피스텔 없음</td></tr>
              ) : d.office.map((r, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: 800 }}>{medal(i + 1)}</td>
                  <td className="l" style={{ fontWeight: 600 }}>{r.name}</td>
                  <td className="l">{r.dong}</td>
                  <td className="l">{r.station || '-'}</td>
                  <td>{n(r.pyeong)}</td>
                  <td title={r.comp >= 400 ? '렌트 조회 상한(400) 도달' : ''}>{fmtComp(r.comp)}</td>
                  <td style={{ color: r.matches != null && r.matches <= 2 ? '#dc2626' : '#94a3b8', fontWeight: r.matches != null && r.matches <= 2 ? 700 : 400 }} title={r.matches != null && r.matches <= 2 ? '표본 부족 — 신뢰도 낮음' : ''}>{n(r.matches)}</td>
                  <td className="occ">{n(r.occ)}%</td>
                  <td className="pos" style={{ fontWeight: 800 }}>{n(r.expNet)}</td>
                  <td style={{ fontWeight: 800, color: '#7c3aed' }}>{n(r.score)}</td>
                  <td className="l">
                    {r.naverUrl && <a className="lnk n" style={{ padding: '3px 8px' }} href={r.naverUrl} target="_blank" rel="noreferrer">부동산</a>}
                    {r.samUrl && <a className="lnk s" style={{ padding: '3px 8px' }} href={r.samUrl} target="_blank" rel="noreferrer">렌트</a>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* 모바일 카드 — 추천 오피스텔(표는 컬럼 많아 잘림) */}
          <div className="listcards">
            {d.office.length === 0 ? (
              <div className="d-empty">조건에 맞는 오피스텔 없음</div>
            ) : d.office.map((r, i) => (
              <div className="mcard" key={i}>
                <div className="mc-main">
                  <div className="mc-name"><span style={{ marginRight: 5 }}>{medal(i + 1)}</span>{r.name}
                    {r.matches != null && r.matches <= 2 && <span style={{ marginLeft: 5, fontSize: 10, fontWeight: 700, color: '#b45309', background: '#fef3c7', borderRadius: 4, padding: '1px 5px' }}>표본부족</span>}
                  </div>
                  <div className="mc-area">{[r.dong, r.station].filter(Boolean).join(' · ')}{r.pyeong ? ` · ${n(r.pyeong)}평` : ''} · 경쟁 {fmtComp(r.comp)}</div>
                  <div className="mc-area">기회점수 <b style={{ color: '#7c3aed' }}>{n(r.score)}</b>
                    {r.naverUrl && <a className="lnk n" style={{ padding: '2px 7px', marginLeft: 6 }} href={r.naverUrl} target="_blank" rel="noreferrer">부동산</a>}
                    {r.samUrl && <a className="lnk s" style={{ padding: '2px 7px', marginLeft: 4 }} href={r.samUrl} target="_blank" rel="noreferrer">렌트</a>}
                  </div>
                </div>
                <div className="mc-nums">
                  <div className="mc-net pos">{n(r.expNet)}<small>만</small></div>
                  <div className="mc-occ">예약 {n(r.occ)}%</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function RecoArea({ title, rows }) {
  return (
    <div className="panel">
      <h3 style={{ margin: '2px 0 8px', fontSize: 14 }}>{title}</h3>
      <div style={{ overflow: 'auto', maxHeight: '64vh' }}>
        <table>
          <thead><tr>{RECO_COLS.map((t, i) => <th key={i} className={i === 1 ? 'l' : ''}>{t}</th>)}</tr></thead>
          <tbody>
            {(!rows || rows.length === 0) ? (
              <tr><td colSpan={7} className="d-empty">조건에 맞는 추천 없음 — 예약률/경쟁 조건을 완화해보세요</td></tr>
            ) : rows.map((r, i) => (
              <tr key={r.name}>
                <td style={{ fontWeight: 800, fontSize: i < 3 ? 15 : 12 }}>{medal(i + 1)}</td>
                <td className="l" style={{ fontWeight: 600 }}>{r.name}
                  {r.n != null && r.n <= 2 && <span title="매칭 표본 1~2건 — 신뢰도 낮음" style={{ marginLeft: 5, fontSize: 10, fontWeight: 700, color: '#b45309', background: '#fef3c7', borderRadius: 4, padding: '1px 5px' }}>표본부족</span>}
                </td>
                <td title={r.comp >= 400 ? '렌트 조회 상한(400) 도달' : ''}>{fmtComp(r.comp)}</td>
                <td style={r.n != null && r.n <= 2 ? { color: '#dc2626', fontWeight: 700 } : { color: '#94a3b8' }}>{n(r.n)}</td>
                <td className="occ">{n(r.occ)}%</td>
                <td className="pos" style={{ fontWeight: 800 }}>{n(r.expNet)}</td>
                <td style={{ fontWeight: 800, color: '#7c3aed' }}>{n(r.score)}</td>
              </tr>
            ))}
          </tbody>
        </table>

        {/* 모바일 카드 — 추천 동/역 */}
        <div className="listcards">
          {(!rows || rows.length === 0) ? (
            <div className="d-empty">조건에 맞는 추천 없음 — 예약률/경쟁 조건을 완화해보세요</div>
          ) : rows.map((r, i) => (
            <div className="mcard" key={r.name}>
              <div className="mc-main">
                <div className="mc-name"><span style={{ marginRight: 5 }}>{medal(i + 1)}</span>{r.name}
                  {r.n != null && r.n <= 2 && <span style={{ marginLeft: 5, fontSize: 10, fontWeight: 700, color: '#b45309', background: '#fef3c7', borderRadius: 4, padding: '1px 5px' }}>표본부족</span>}
                </div>
                <div className="mc-area">경쟁 {fmtComp(r.comp)} · 매칭 {n(r.n)} · 기회점수 <b style={{ color: '#7c3aed' }}>{n(r.score)}</b></div>
              </div>
              <div className="mc-nums">
                <div className="mc-net pos">{n(r.expNet)}<small>만</small></div>
                <div className="mc-occ">예약 {n(r.occ)}%</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
