import { useEffect } from 'react'
import { TYPES, GROUPS, won, fmtVal, addrOf, roadOf, titleOf } from './helpers.js'
import SamArea from './SamArea.jsx'
import AptPrice from './AptPrice.jsx'

// 주소(+건물명)로 네이버 통합검색. 매물번호 딥링크가 죽어 대체한 경로.
function naverSearch(x, addr) {
  const bn = (x.building_name || (x.geo || {}).building || '').trim()
  const q = [addr, bn && !bn.endsWith('동') ? bn : ''].filter(Boolean).join(' ')
  return 'https://m.search.naver.com/search.naver?query=' + encodeURIComponent(q)
}

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
  const title = titleOf(x, TYPES[tc])
  const addr = addrOf(x)
  const road = roadOf(x)

  return (
    <div className="overlay on" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal">
        <div className="mhead">
          <div>
            <h2>{title} <span className="chip">{TYPES[tc] || tc}</span></h2>
            <div className="ma">{addr}{road ? ' · ' + road : ''}</div>
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
          <AptPrice ap={x.apt_price} trades={x.apt_trades || []} />
          <SamArea sa={x.sam_area} />
          {/* 네이버 매물번호 딥링크는 네이버가 경로를 없애 404가 뜬다(2026-09) → 주소로 검색 */}
          <a className="mlink" href={naverSearch(x, addr)} target="_blank" rel="noreferrer">
            네이버에서 검색 →
          </a>
        </div>
      </div>
    </div>
  )
}
