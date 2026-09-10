import { TYPES, won, fmtVal, ageBadge, addrOf, roadOf, titleOf } from './helpers.js'
import SamArea from './SamArea.jsx'
import AptPrice from './AptPrice.jsx'

// 매물 카드 하나. 기존 renderGrid 이식.
export default function Card({ x, onClick }) {
  const tc = x.building_type_code || ''
  const title = titleOf(x, TYPES[tc])
  const addr = addrOf(x)
  const road = roadOf(x)
  const rent = x.rent_monthly
  const specs = []
  if (x.pyeong) specs.push(`${x.pyeong}평`)
  if (x.area_exclusive_m2) specs.push(`전용 ${x.area_exclusive_m2}㎡`)
  if (x.floor_current != null) specs.push(`${x.floor_current}층`)
  else if (x.floorinfo) specs.push(`${x.floorinfo}층`)
  if (x.rooms != null) specs.push(`방${x.rooms}`)
  if (x.direction) specs.push(x.direction.replace(' (거실 기준)', ''))
  const sub = x.subway_station ? `${x.subway_station} ${x.subway_distance_m ? x.subway_distance_m + 'm' : ''}` : ''
  const smry = fmtVal(x.summary) || ''
  const age = ageBadge(x.confirmed_at)

  return (
    <div className={`lst${age?.kind === 'stale' ? ' stale' : ''}`} onClick={onClick}>
      <span className={`badge b-${tc}`}>{TYPES[tc] || tc}</span>
      {age && <span className={`agebadge a-${age.kind}`} title={age.title}>{age.label}</span>}
      <div className="nm">{title}</div>
      <div className="addr" title={addr}>{addr}</div>
      {road && <div className="addr road" title={road}>{road}</div>}
      <div className="price">
        <span className="dep">보증 {won(x.deposit)}</span> / 월 {rent ? <em>{won(rent)}</em> : '매매/기타'}
        {x.maintenance_monthly > 0 && (
          <span className="mut" style={{ fontWeight: 600, fontSize: 11.5 }}> +관리 {won(x.maintenance_monthly)}만</span>
        )}
      </div>
      <div className="specs">{specs.map((s, i) => <span className="spec" key={i}>{s}</span>)}</div>
      {sub && <div className="sub">{sub}</div>}
      {smry && <div className="smry">{smry}</div>}
      <AptPrice ap={x.apt_price} compact />
      <SamArea sa={x.sam_area} />
    </div>
  )
}
