import { TYPES, won, fmtVal } from './helpers.js'
import SamArea from './SamArea.jsx'

// 매물 카드 하나. 기존 renderGrid 이식.
export default function Card({ x, onClick }) {
  const tc = x.building_type_code || ''
  const title = x.building_name || TYPES[tc] || '매물'
  const addr = x.jibun_address || x.road_address || ''
  const rent = x.rent_monthly
  const specs = []
  if (x.pyeong) specs.push(`${x.pyeong}평`)
  if (x.area_exclusive_m2) specs.push(`전용 ${x.area_exclusive_m2}㎡`)
  if (x.floor_current != null) specs.push(`${x.floor_current}층`)
  if (x.rooms != null) specs.push(`방${x.rooms}`)
  if (x.direction) specs.push(x.direction.replace(' (거실 기준)', ''))
  const sub = x.subway_station ? `${x.subway_station} ${x.subway_distance_m ? x.subway_distance_m + 'm' : ''}` : ''
  const smry = fmtVal(x.summary) || ''

  return (
    <div className="lst" onClick={onClick}>
      <span className={`badge b-${tc}`}>{TYPES[tc] || tc}</span>
      <div className="nm">{title}</div>
      <div className="addr">{addr}</div>
      <div className="price">
        <span className="dep">보증 {won(x.deposit)}</span> / 월 {rent ? <em>{won(rent)}</em> : '매매/기타'}
        {x.maintenance_monthly > 0 && (
          <span className="mut" style={{ fontWeight: 600, fontSize: 11.5 }}> +관리 {won(x.maintenance_monthly)}만</span>
        )}
      </div>
      <div className="specs">{specs.map((s, i) => <span className="spec" key={i}>{s}</span>)}</div>
      {sub && <div className="sub">{sub}</div>}
      {smry && <div className="smry">{smry}</div>}
      <SamArea sa={x.sam_area} />
    </div>
  )
}
