import { useState, useEffect } from 'react'
import { getJSON } from '../shared/api.js'
import './styles.css'
import FilterPanel from './FilterPanel.jsx'
import BuildingView from './BuildingView.jsx'
import RankingView from './RankingView.jsx'
import TrendView from './TrendView.jsx'
import OptionView from './OptionView.jsx'

const TABS = [
  { id: 'building', label: '건물 인기' },
  { id: 'ranking', label: '🏆 랭킹' },
  { id: 'trend', label: '지역 트렌드' },
  { id: 'option', label: '옵션 영향' },
]
const DEFAULT_F = { sidos: [], sigungu: '', dong: '', btype: '', pmin: '', pmax: '', wmin: '', wmax: '' }

export default function App() {
  const [facets, setFacets] = useState(null)
  const [f, setF] = useState(DEFAULT_F)
  const [view, setView] = useState('building')
  const [runSeq, setRunSeq] = useState(0)

  useEffect(() => { getJSON('api/facets').then(setFacets).catch(() => {}) }, [])

  if (!facets) return <div style={{ padding: 40, color: '#94a3b8' }}>불러오는 중…</div>

  const isDb = facets.source && facets.source.startsWith('DB')

  return (
    <>
      <header>
        <h1>🛋️ 삼삼 옵션별 공실/예약률 분석</h1>
        <p>임대인 관점 — "이 옵션 없어도 잘 나갈까?" · 옵션 <b>있는 집</b> vs <b>없는 집</b>의 평균 예약률 비교 · 최근 1달 기준</p>
        <span className={`src ${isDb ? 'db' : 'sample'}`}>출처: {facets.source} · 총 {facets.total.toLocaleString()}건</span>
        {facets.occ_window && <span style={{ display: 'block', marginTop: 6, fontSize: 11.5, color: '#cbd5e1' }}>🗓 {facets.occ_window}</span>}
      </header>

      <div className="wrap">
        <FilterPanel facets={facets} f={f} setF={setF} onRun={() => setRunSeq((n) => n + 1)} />

        <div className="tabs2">
          {TABS.map((t) => (
            <button key={t.id} className={view === t.id ? 'on' : ''} onClick={() => setView(t.id)}>{t.label}</button>
          ))}
        </div>

        {view === 'building' && <BuildingView filters={f} runSeq={runSeq} />}
        {view === 'ranking' && <RankingView />}
        {view === 'trend' && <TrendView filters={f} runSeq={runSeq} />}
        {view === 'option' && <OptionView filters={f} runSeq={runSeq} />}
      </div>
    </>
  )
}
