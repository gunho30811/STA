import { useState, useEffect } from 'react'
import { fetchFacets } from '../shared/api.js'
import './styles.css'
import ProfitList from './ProfitList.jsx'
import RankTab from './RankTab.jsx'
import RecoTab from './RecoTab.jsx'

const TABS = [
  { id: 'list', label: '매물 수익성' },
  { id: 'rank', label: '🏆 순위 (동·역 베스트)' },
  { id: 'reco', label: '🎯 신규진입 추천' },
]

export default function App() {
  const [facets, setFacets] = useState(null)
  const [tab, setTab] = useState('list')
  const [month, setMonth] = useState('')

  useEffect(() => { fetchFacets().then(setFacets).catch(() => {}) }, [])

  if (!facets) return <div style={{ padding: 40, color: '#94a3b8' }}>불러오는 중…</div>

  const months = facets.months || []

  return (
    <>
      <header>
        <h1>🏠 삼삼 × 부동산 단기임대 수익성</h1>
        <p>삼삼 단기임대 풀가동 시 부동산 장기월세 대비 최대수익·순수익 + 동/역 수요(예약률) · 단위 만원 · 행 클릭 시 오른쪽 상세</p>
      </header>

      <div className="tabs">
        {TABS.map((t) => (
          <button key={t.id} className={tab === t.id ? 'on' : ''} onClick={() => setTab(t.id)}>{t.label}</button>
        ))}
      </div>

      <div className="wrap">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '2px 0 12px', flexWrap: 'wrap' }}>
          <label style={{ fontWeight: 700, fontSize: 13 }}>📅 기준 월</label>
          <select value={month} onChange={(e) => setMonth(e.target.value)}
            style={{ padding: '6px 10px', border: '1px solid #d1d5db', borderRadius: 7, fontSize: 13 }}>
            <option value="">전체 (오늘기준 1달·롤링)</option>
            {months.map((m) => <option key={m} value={m}>{m.replace('-', '년 ')}월</option>)}
          </select>
          <span className="legend" style={{ margin: 0 }}>
            {months.length
              ? '달력월별(예: 2026-08 예약률)은 삼삼 재크롤분부터 채워져요.'
              : '아직 달력월별 데이터가 없어요 — 삼삼 재크롤 후 채워집니다.'}
          </span>
        </div>

        {tab === 'list' && <ProfitList facets={facets} month={month} />}
        {tab === 'rank' && <RankTab month={month} />}
        {tab === 'reco' && <RecoTab month={month} />}
      </div>
    </>
  )
}
