import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { getJSON } from '../shared/api.js'

// 지도 검색 — 렌트(삼삼)·부동산(네이버) 매물 + 동별 예약률 원.
// 뷰포트(bbox) 이동/줌 때마다 /api/map 재조회(디바운스). 개별 마커는 줌 14+에서만(과밀 방지).
const SEOUL = [37.5665, 126.978]
const MARKER_ZOOM = 14   // 이 줌 미만이면 원(동별 예약률)만 표시

function occColor(occ) {
  if (occ == null) return '#94a3b8'
  if (occ >= 60) return '#059669'   // 잘 나감(초록)
  if (occ >= 30) return '#f59e0b'   // 중간(주황)
  return '#dc2626'                  // 낮음(빨강)
}

export default function MapView({ filters }) {
  const boxRef = useRef(null)
  const mapRef = useRef(null)
  const layersRef = useRef(null)   // {circles, rent, naver}
  const [show, setShow] = useState({ rent: true, naver: true, circles: true })
  const showRef = useRef(show)
  const [stat, setStat] = useState('')
  const btype = filters.btype || ''
  const btypeRef = useRef(btype)

  useEffect(() => { showRef.current = show }, [show])
  useEffect(() => { btypeRef.current = btype }, [btype])

  useEffect(() => {
    if (mapRef.current) return
    const map = L.map(boxRef.current, { center: SEOUL, zoom: 13, zoomControl: true })
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '&copy; OpenStreetMap',
    }).addTo(map)
    const layers = {
      circles: L.layerGroup().addTo(map),
      rent: L.layerGroup().addTo(map),
      naver: L.layerGroup().addTo(map),
    }
    mapRef.current = map
    layersRef.current = layers

    let timer = null
    const refetch = () => { clearTimeout(timer); timer = setTimeout(load, 350) }

    async function load() {
      const b = map.getBounds()
      const zoom = map.getZoom()
      const p = new URLSearchParams({
        min_lat: b.getSouth(), max_lat: b.getNorth(),
        min_lng: b.getWest(), max_lng: b.getEast(),
      })
      if (btypeRef.current) p.set('building_type', btypeRef.current)
      // 낮은 줌에선 마커 요청 자체를 끔(응답 가볍게) — 원만.
      if (zoom < MARKER_ZOOM) { p.set('rent', '0'); p.set('naver', '0') }
      let d
      try { d = await getJSON('api/map?' + p.toString()) } catch { return }
      const sh = showRef.current

      layers.circles.clearLayers()
      if (sh.circles) {
        for (const c of d.circles || []) {
          L.circle([c.lat, c.lng], {
            radius: 420, color: occColor(c.occ), weight: 1.5,
            fillColor: occColor(c.occ), fillOpacity: 0.14, interactive: false,
          }).addTo(layers.circles)
          L.marker([c.lat, c.lng], {
            interactive: false,
            icon: L.divIcon({
              className: 'occ-badge-wrap', iconSize: null,
              html: `<div class="occ-badge" style="border-color:${occColor(c.occ)};color:${occColor(c.occ)}">` +
                    `${c.dong}<br><b>${c.occ}%</b><span class="occ-n">·${c.n}</span></div>`,
            }),
          }).addTo(layers.circles)
        }
      }

      layers.rent.clearLayers()
      if (sh.rent) {
        for (const r of d.rent || []) {
          L.circleMarker([r.lat, r.lng], {
            radius: 6, color: '#123A6D', weight: 1.5, fillColor: '#123A6D', fillOpacity: 0.85,
          }).bindPopup(
            `<b>${r.name || '(이름없음)'}</b><br>${r.btype} · ${r.pyeong ?? '-'}평 · 주당 ${r.week ?? '-'}만<br>` +
            `예약률 <b style="color:${occColor(r.occ)}">${r.occ}%</b>` +
            (r.url ? `<br><a href="${r.url}" target="_blank" rel="noreferrer">렌트 매물 →</a>` : ''),
          ).addTo(layers.rent)
        }
      }

      layers.naver.clearLayers()
      if (sh.naver) {
        for (const n of d.naver || []) {
          L.circleMarker([n.lat, n.lng], {
            radius: 5, color: '#0d9488', weight: 1.5, fillColor: '#14b8a6', fillOpacity: 0.75,
          }).bindPopup(
            `<b>${n.name || '(이름없음)'}</b><br>보증금 ${n.dep ?? '-'} / 월세 ${n.rent ?? '-'}만` +
            `${n.m2 ? ` · ${Math.round(n.m2 / 3.30578 * 10) / 10}평(${n.m2}㎡)` : ''}${n.floor != null ? ` · ${n.floor}층` : ''}` +
            `<br><a href="${n.url}" target="_blank" rel="noreferrer">부동산 매물 →</a>`,
          ).addTo(layers.naver)
        }
      }

      setStat(zoom < MARKER_ZOOM
        ? `동별 예약률 ${d.circles?.length ?? 0}개 — 확대하면 매물 마커 표시`
        : `렌트 ${d.rent?.length ?? 0} · 부동산 ${d.naver?.length ?? 0} · 동 ${d.circles?.length ?? 0}`)
    }

    map.on('moveend zoomend', refetch)
    load()
    return () => { clearTimeout(timer); map.off(); map.remove(); mapRef.current = null }
    // eslint-disable-next-line
  }, [])

  // 토글/유형 변경 시 재조회
  useEffect(() => {
    if (mapRef.current) mapRef.current.fire('moveend')
  }, [show, btype])

  const Toggle = ({ k, label, color }) => (
    <label className={'map-tog' + (show[k] ? ' on' : '')} style={show[k] ? { borderColor: color, color } : undefined}>
      <input type="checkbox" checked={show[k]} onChange={() => setShow((s) => ({ ...s, [k]: !s[k] }))} />{label}
    </label>
  )

  return (
    <div className="panel">
      <h2 className="sec">🗺️ 지도 검색 — 렌트·부동산 매물과 <b>동별 예약률</b>을 지도에서</h2>
      <p className="hint">원(동그라미)=그 동네 렌트 평균 예약률(매물 3개↑ 동만) · 보라점=렌트 매물 · 청록점=부동산 매물 ·
        줌 {MARKER_ZOOM}+ 에서 개별 매물 표시 · 위 필터의 <b>건물유형</b> 적용(렌트·원). 마커 클릭=상세.</p>
      <div className="map-bar">
        <Toggle k="circles" label="동별 예약률 원" color="#059669" />
        <Toggle k="rent" label="렌트 매물" color="#123A6D" />
        <Toggle k="naver" label="부동산 매물" color="#0d9488" />
        <span className="mut" style={{ fontSize: 12 }}>{stat}</span>
      </div>
      <div ref={boxRef} className="map-box" />
    </div>
  )
}
