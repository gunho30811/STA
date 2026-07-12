import { useEffect, useState } from 'react'
import { getJSON, fmt as n } from '../shared/api.js'

// 순위(동/역) 한 행을 클릭하면 그 지역을 이루는 근거 매물들을 보여주는 모달 (QA #8).
// props: field('dong'|'station'), label, month, rooms, onClose
export default function RankDetailModal({ field, label, month, rooms, onClose }) {
  const [data, setData] = useState(null)

  useEffect(() => {
    const p = new URLSearchParams({ field, label })
    if (month) p.set('month', month)
    if (rooms) p.set('rooms', rooms)
    getJSON('api/rank_detail?' + p.toString()).then(setData).catch(() => setData({ items: [], total: 0 }))
  }, [field, label, month, rooms])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="rd-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="rd-modal">
        <div className="rd-head">
          <div>
            <div className="rd-title">{label}</div>
            <div className="rd-sub">{field === 'dong' ? '동' : '역'} 순위 근거 — 이 지역을 이루는 삼삼 매물 {data ? `${data.total}건` : ''}</div>
          </div>
          <button className="rd-x" onClick={onClose}>&times;</button>
        </div>
        <div className="rd-body">
          {!data ? (
            <div style={{ padding: 30, color: '#94a3b8', textAlign: 'center' }}>불러오는 중…</div>
          ) : data.items.length === 0 ? (
            <div style={{ padding: 30, color: '#94a3b8', textAlign: 'center' }}>매물 없음</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th className="l">매물명</th><th>평</th><th>주당</th><th>예약률%</th>
                  <th>기대월순수익</th><th>매칭</th><th>부동산월세</th><th className="l">링크</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((r, i) => (
                  <tr key={i}>
                    <td className="l" style={{ fontWeight: 600 }}>{r.name}</td>
                    <td>{n(r.pyeong)}</td>
                    <td>{n(r.wk)}</td>
                    <td className="occ">{n(r.occ)}%</td>
                    <td className={r.expNet != null && r.expNet >= 0 ? 'pos' : 'neg'} style={{ fontWeight: 800 }}>{n(r.expNet)}</td>
                    <td style={r.matches != null && r.matches <= 2 ? { color: '#dc2626', fontWeight: 700 } : { color: '#94a3b8' }}
                      title={r.matches != null && r.matches <= 2 ? '표본 부족' : ''}>{n(r.matches)}</td>
                    <td>{n(r.nRent)}</td>
                    <td className="l">
                      {r.samUrl && <a className="lnk s" style={{ padding: '3px 8px' }} href={r.samUrl} target="_blank" rel="noreferrer">삼삼</a>}
                      {r.naverUrl && <a className="lnk n" style={{ padding: '3px 8px' }} href={r.naverUrl} target="_blank" rel="noreferrer">부동산</a>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}
