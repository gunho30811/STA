import { useState, useEffect, useMemo, useCallback } from 'react'
import { getJSON } from '../shared/api.js'
import './styles.css'
import Card from './Card.jsx'
import Modal from './Modal.jsx'

const uniq = (arr) => [...new Set(arr)].filter(Boolean).sort()

export default function App() {
  const [facets, setFacets] = useState(null)
  const [stats, setStats] = useState(null)

  // 필터 상태
  const [selTypes, setSelTypes] = useState([])
  const [selRooms, setSelRooms] = useState([])
  const [casc, setCasc] = useState({ sido: '', sigun: '', gu: '', dong: '' })
  const [selRegions, setSelRegions] = useState([])   // [{enc,label}]
  const [stationInput, setStationInput] = useState('')
  const [selStations, setSelStations] = useState([])
  const [radius, setRadius] = useState('1000')
  const [f, setF] = useState({ kw: '', depmin: '', depmax: '', rentmin: '', rentmax: '', pmin: '', pmax: '', netmin: '', sort: 'recent', office: false })

  const [res, setRes] = useState({ items: [], total: 0, page: 1, pages: 1 })
  const [modal, setModal] = useState(null)

  const regions = facets?.regions || []
  const stationSet = useMemo(() => new Set(facets?.stations || []), [facets])

  useEffect(() => {
    getJSON('api/facets').then(setFacets).catch(() => {})
    getJSON('api/stats').then(setStats).catch(() => {})
  }, [])

  // 종속 드롭다운 옵션
  const sidos = uniq(regions.map((r) => r[0]))
  const siguns = uniq(regions.filter((r) => !casc.sido || r[0] === casc.sido).map((r) => r[1]))
  const gus = uniq(regions.filter((r) => (!casc.sido || r[0] === casc.sido) && (!casc.sigun || r[1] === casc.sigun)).map((r) => r[2]))
  const dongs = uniq(regions.filter((r) => (!casc.sido || r[0] === casc.sido) && (!casc.sigun || r[1] === casc.sigun) && (!casc.gu || r[2] === casc.gu)).map((r) => r[3]))

  const doSearch = useCallback(async (page) => {
    const p = new URLSearchParams()
    if (selTypes.length) p.set('types', selTypes.join(','))
    selRooms.forEach((r) => p.append('rooms', r))

    // 지역: 현재 셀렉트 선택값(칩 없이 단일선택도 동작) + 추가한 칩들
    const cascEnc = [casc.sido, casc.sigun, casc.gu, casc.dong].join('|')
    if (cascEnc !== '|||' && !selRegions.some((r) => r.enc === cascEnc)) p.append('region', cascEnc)
    selRegions.forEach((r) => p.append('region', r.enc))

    // 역 반경: 입력창 잔여 역명 자동 반영 + 선택 역들
    let stns = selStations
    const leftover = stationInput.trim()
    if (leftover) {
      const s = stationSet.has(leftover) ? leftover : [...stationSet].find((n) => n === leftover + '역' || n.includes(leftover))
      if (s && !stns.includes(s)) { stns = [...stns, s]; setSelStations(stns); setStationInput('') }
    }
    if (stns.length) { stns.forEach((s) => p.append('station', s)); p.set('radius', radius) }

    if (f.kw) p.set('keyword', f.kw)
    if (f.depmin) p.set('deposit_min', f.depmin)
    if (f.depmax) p.set('deposit_max', f.depmax)
    if (f.rentmin) p.set('rent_min', f.rentmin)
    if (f.rentmax) p.set('rent_max', f.rentmax)
    if (f.pmin) p.set('pyeong_min', f.pmin)
    if (f.pmax) p.set('pyeong_max', f.pmax)
    if (f.netmin) p.set('net_min', f.netmin)
    if (f.office) p.set('office', '1')
    p.set('sort', f.sort); p.set('page', page); p.set('size', 24)

    const r = await getJSON('api/listings?' + p.toString())
    setRes(r)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [selTypes, selRooms, casc, selRegions, selStations, stationInput, stationSet, radius, f])

  // 최초 로드(facets 준비 후 1회)
  useEffect(() => { if (facets) doSearch(1) /* eslint-disable-next-line */ }, [facets])

  const toggle = (list, setList, v) => setList(list.includes(v) ? list.filter((x) => x !== v) : [...list, v])

  const addRegion = () => {
    const parts = [casc.sido, casc.sigun, casc.gu, casc.dong]
    if (!parts.some(Boolean)) return
    const enc = parts.join('|'), label = parts.filter(Boolean).join(' ')
    if (selRegions.some((r) => r.enc === enc)) return
    setSelRegions([...selRegions, { enc, label }])
  }
  const addStation = () => {
    const s0 = stationInput.trim()
    if (!s0) return
    let s = s0
    if (!stationSet.has(s)) {
      const hit = [...stationSet].find((n) => n === s + '역' || n.includes(s))
      if (!hit) { alert('역 목록에 없습니다: ' + s); return }
      s = hit
    }
    if (!selStations.includes(s)) setSelStations([...selStations, s])
    setStationInput('')
  }
  const reset = () => {
    setSelTypes([]); setSelRooms([]); setSelRegions([]); setSelStations([]); setStationInput('')
    setCasc({ sido: '', sigun: '', gu: '', dong: '' }); setRadius('1000')
    setF({ kw: '', depmin: '', depmax: '', rentmin: '', rentmax: '', pmin: '', pmax: '', netmin: '', sort: 'recent', office: false })
    getJSON('api/listings?sort=recent&page=1&size=24').then(setRes)
  }

  const openModal = async (base) => {
    setModal(base)   // 리스트 값으로 즉시
    try {
      const full = await getJSON('api/detail/' + base.article_no)
      if (full && !full.error) setModal((cur) => (cur && cur.article_no === base.article_no ? { ...base, ...full } : cur))
    } catch { /* noop */ }
  }

  if (!facets || !stats) return <div style={{ padding: 40, color: '#94a3b8' }}>불러오는 중…</div>

  return (
    <>
      <header>
        <h1>🏙️ 수도권 부동산 매물 뷰어</h1>
        <p>서울·경기·인천 부동산 · 아파트/오피스텔/빌라/원룸/단독·다가구 · 카드 클릭 시 상세 전체 보기 · 단위 만원</p>
      </header>

      <div className="wrap">
        <div className="cards">
          <div className="card"><div className="lbl">총 매물수</div><div className="val">{stats.total.toLocaleString()}</div></div>
          <div className="card"><div className="lbl">수집 동</div><div className="val">{stats.dong_count}<small> 개동</small></div></div>
          <div className="card"><div className="lbl">월세 중앙값</div><div className="val">{stats.rent_median ?? '-'}<small> 만원</small></div></div>
          <div className="card" style={{ flex: 1, minWidth: 260 }}>
            <div className="lbl">타입별 분포</div>
            <div className="typechips">{stats.by_type.map((t) => <span className="chip" key={t.code}>{t.name} {t.count.toLocaleString()}</span>)}</div>
          </div>
        </div>

        <div className="panel">
          <div className="fg" style={{ marginBottom: 13 }}>
            <label>매물 타입 (복수 선택)</label>
            <div className="typebtns">
              {facets.types.map((t) => (
                <button key={t.code} className={selTypes.includes(t.code) ? 'on' : ''}
                  onClick={() => toggle(selTypes, setSelTypes, t.code)}>{t.name}</button>
              ))}
            </div>
          </div>
          <div className="fg" style={{ marginBottom: 13 }}>
            <label>방 개수 (복수 선택)</label>
            <div className="typebtns">
              {[['1', '1룸'], ['2', '2룸'], ['3', '3룸'], ['4+', '4룸+']].map(([code, label]) => (
                <button key={code} className={selRooms.includes(code) ? 'on' : ''}
                  onClick={() => toggle(selRooms, setSelRooms, code)}>{label}</button>
              ))}
            </div>
          </div>
          <div className="fg" style={{ marginBottom: 13 }}>
            <label>지역 (좁힌 뒤 ➕추가로 여러 곳 선택 가능)</label>
            <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', alignItems: 'center' }}>
              <select value={casc.sido} onChange={(e) => setCasc({ sido: e.target.value, sigun: '', gu: '', dong: '' })}>
                <option value="">시/도 전체</option>{sidos.map((s) => <option key={s}>{s}</option>)}
              </select>
              <select value={casc.sigun} onChange={(e) => setCasc((c) => ({ ...c, sigun: e.target.value, gu: '', dong: '' }))}>
                <option value="">시/군 전체</option>{siguns.map((s) => <option key={s}>{s}</option>)}
              </select>
              <select value={casc.gu} onChange={(e) => setCasc((c) => ({ ...c, gu: e.target.value, dong: '' }))}>
                <option value="">구 전체</option>{gus.map((s) => <option key={s}>{s}</option>)}
              </select>
              <select value={casc.dong} onChange={(e) => setCasc((c) => ({ ...c, dong: e.target.value }))}>
                <option value="">동 전체</option>{dongs.map((s) => <option key={s}>{s}</option>)}
              </select>
              <button type="button" className="btn" style={{ background: '#e0e7ff', color: '#3730a3' }} onClick={addRegion}>➕ 지역 추가</button>
            </div>
            <div className="selchips">
              {selRegions.map((r, i) => (
                <span className="selchip" key={r.enc}>📍 {r.label}
                  <span className="rm" onClick={() => setSelRegions(selRegions.filter((_, j) => j !== i))}>×</span>
                </span>
              ))}
            </div>
          </div>
          <div className="fg" style={{ marginBottom: 13 }}>
            <label>🚇 역 반경 검색 (역 여러 개 선택 · 선택 역 반경 내 매물 모두)</label>
            <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', alignItems: 'center' }}>
              <input list="station-list" value={stationInput} placeholder="역 이름 (예: 강남, 홍대입구, 이대)"
                style={{ minWidth: 200, padding: '7px 9px', border: '1px solid #d1d5db', borderRadius: 7, fontSize: 13 }}
                onChange={(e) => setStationInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addStation() } }} />
              <datalist id="station-list">{facets.stations.map((s) => <option key={s} value={s} />)}</datalist>
              <button type="button" className="btn" style={{ background: '#dbeafe', color: '#075985' }} onClick={addStation}>➕ 역 추가</button>
              <select value={radius} onChange={(e) => setRadius(e.target.value)}
                style={{ padding: '7px 9px', border: '1px solid #d1d5db', borderRadius: 7, fontSize: 13 }}>
                <option value="500">반경 500m</option><option value="1000">반경 1km</option>
                <option value="2000">반경 2km</option><option value="3000">반경 3km</option>
              </select>
            </div>
            <div className="selchips">
              {selStations.map((s, i) => (
                <span className="selchip stn" key={s}>🚇 {s}
                  <span className="rm" onClick={() => setSelStations(selStations.filter((_, j) => j !== i))}>×</span>
                </span>
              ))}
            </div>
          </div>
          <div className="filters">
            <div className="fg"><label>키워드(건물/요약/주소/역)</label>
              <input value={f.kw} placeholder="예: 래미안, 역세권" onChange={(e) => setF({ ...f, kw: e.target.value })}
                onKeyDown={(e) => { if (e.key === 'Enter') doSearch(1) }} /></div>
            <div className="fg"><label>보증금(만)</label><div style={{ display: 'flex', gap: 4 }}>
              <input type="number" value={f.depmin} placeholder="최소" onChange={(e) => setF({ ...f, depmin: e.target.value })} />
              <input type="number" value={f.depmax} placeholder="최대" onChange={(e) => setF({ ...f, depmax: e.target.value })} /></div></div>
            <div className="fg"><label>월세(만)</label><div style={{ display: 'flex', gap: 4 }}>
              <input type="number" value={f.rentmin} placeholder="최소" onChange={(e) => setF({ ...f, rentmin: e.target.value })} />
              <input type="number" value={f.rentmax} placeholder="최대" onChange={(e) => setF({ ...f, rentmax: e.target.value })} /></div></div>
            <div className="fg"><label>평수</label><div style={{ display: 'flex', gap: 4 }}>
              <input type="number" value={f.pmin} placeholder="최소" onChange={(e) => setF({ ...f, pmin: e.target.value })} />
              <input type="number" value={f.pmax} placeholder="최대" onChange={(e) => setF({ ...f, pmax: e.target.value })} /></div></div>
            <div className="fg"><label>💰 월순수익(만) 이상</label>
              <input type="number" value={f.netmin} placeholder="예: 0" style={{ width: 110 }} onChange={(e) => setF({ ...f, netmin: e.target.value })} /></div>
            <div className="fg"><label>정렬</label>
              <select value={f.sort} onChange={(e) => setF({ ...f, sort: e.target.value })}>
                <option value="recent">최신순</option>
                <option value="net_desc">💰 순수익 높은순</option>
                <option value="rent_asc">월세 낮은순</option>
                <option value="rent_desc">월세 높은순</option>
                <option value="deposit_asc">보증금 낮은순</option>
                <option value="deposit_desc">보증금 높은순</option>
                <option value="area_desc">면적 넓은순</option>
                <option value="area_asc">면적 좁은순</option>
              </select></div>
            <div className="fg"><label>🏢 업무용</label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 700, cursor: 'pointer', padding: '7px 0' }}>
                <input type="checkbox" checked={f.office} onChange={(e) => { const office = e.target.checked; setF({ ...f, office }); }} /> 업무용 오피스텔만
                <span style={{ fontWeight: 400 }}>{facets.office != null ? `(${facets.office.toLocaleString()}건)` : ''}</span>
              </label></div>
            <div className="fg"><label>&nbsp;</label><button className="btn btn-go" onClick={() => doSearch(1)}>검색</button></div>
            <div className="fg"><label>&nbsp;</label><button className="btn btn-reset" onClick={reset}>초기화</button></div>
          </div>
        </div>

        <div className="toolbar"><div className="cnt">검색결과 <b>{res.total.toLocaleString()}</b> 건</div></div>
        <div className="grid">
          {res.items.length === 0
            ? <div className="empty" style={{ gridColumn: '1/-1' }}>조건에 맞는 매물이 없습니다.</div>
            : res.items.map((x) => <Card key={x.article_no} x={x} onClick={() => openModal(x)} />)}
        </div>
        <GangnamPager page={res.page} pages={res.pages} onGo={doSearch} />
      </div>

      <Modal item={modal} onClose={() => setModal(null)} />
    </>
  )
}

// 페이지네이션(기존 gangnam renderPager: 현재±3 + 1/last + …)
function GangnamPager({ page, pages, onGo }) {
  if (pages <= 1) return null
  const out = []
  const add = (nk, label, on) => out.push(
    <button key={`p${nk}-${label || ''}`} className={on ? 'on' : ''} onClick={() => onGo(nk)}>{label || nk}</button>,
  )
  if (page > 1) add(page - 1, '‹')
  const s = Math.max(1, page - 3), e = Math.min(pages, page + 3)
  if (s > 1) add(1, '1')
  if (s > 2) out.push(<span key="e1" className="ell">…</span>)
  for (let i = s; i <= e; i++) add(i, null, i === page)
  if (e < pages - 1) out.push(<span key="e2" className="ell">…</span>)
  if (e < pages) add(pages, String(pages))
  if (page < pages) add(page + 1, '›')
  return <div className="pager">{out}</div>
}
