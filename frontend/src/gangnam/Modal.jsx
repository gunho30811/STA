import { useEffect } from 'react'
import { TYPES, GROUPS, won, fmtVal } from './helpers.js'
import SamArea from './SamArea.jsx'

// 상세 모달. 기존 renderM 이식. item은 리스트값+DB 상세 병합본.
export default function Modal({ item, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!item) return null
  const x = item
  const tc = x.building_type_code || ''
  const title = x.building_name || TYPES[tc] || '매물'

  return (
    <div className="overlay on" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal">
        <div className="mhead">
          <div>
            <h2>{title} <span className="chip">{TYPES[tc] || tc}</span></h2>
            <div className="ma">{x.jibun_address || ''} {x.road_address ? '· ' + x.road_address : ''}</div>
          </div>
          <button className="x" onClick={onClose}>&times;</button>
        </div>
        <div className="mbody">
          <div className="mprice">
            <span className="dep">보증금 {won(x.deposit)}</span> / {x.rent_monthly ? <>월 <em>{won(x.rent_monthly)}</em></> : '매매/기타'}
            {x.maintenance_monthly ? <span style={{ fontSize: 13, color: '#9ca3af' }}> · 관리비 {won(x.maintenance_monthly)}</span> : null}
          </div>
          {GROUPS.map(([gname, fields]) => {
            const rows = fields
              .map(([key, label, unit]) => [key, label, unit, fmtVal(x[key])])
              .filter(([, , , val]) => val != null)
            if (!rows.length) return null
            return (
              <div className="sec" key={gname}>
                <h3>{gname}</h3>
                <div className="kv">
                  {rows.map(([key, label, unit, val]) => (
                    <div className="row" key={key}>
                      <span className="k">{label}</span>
                      <span className="v">{val}{unit ? ' ' + unit : ''}</span>
                    </div>
                  ))}
                </div>
              </div>
            )
          })}
          <SamArea sa={x.sam_area} />
          {x.url && <a className="mlink" href={x.url} target="_blank" rel="noreferrer">부동산에서 보기 →</a>}
        </div>
      </div>
    </div>
  )
}
