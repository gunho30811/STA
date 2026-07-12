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

  // facets를 기다리며 화면 전체를 막지 않는다 — 헤더·탭은 바로 그리고 데이터만 안쪽에서 로딩.
  const months = facets?.months || []
  const demo = facets?.demo === true   // 미로그인 데모 모드(일부만 공개 → 회원가입 유도)
  const goSignup = () => { window.location.href = '/auth/signup' }

  // 데모에서는 매물 탭만 열람 가능, 순위/추천은 회원 전용.
  const onTab = (id) => {
    if (demo && id !== 'list') { goSignup(); return }
    setTab(id)
  }

  return (
    <>
      <header>
        <h1>🏠 삼삼 × 부동산 단기임대 수익성</h1>
        <p>삼삼 단기임대 풀가동 시 부동산 장기월세 대비 최대수익·순수익 + 동/역 수요(예약률) · 단위 만원 · 행 클릭 시 오른쪽 상세</p>
      </header>

      {demo && (
        <div style={{ background: '#1e293b', color: '#e2e8f0', padding: '10px 22px', fontSize: 13.5, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'center' }}>
          <span>🔒 <b>데모</b> — 지금은 기대 월순수익 상위 일부만 보여요. 회원가입하면 <b>전체 매물 · 필터 · 순위 · 신규진입 추천</b>까지 전부.</span>
          <a href="/auth/signup" style={{ background: '#2563eb', color: '#fff', textDecoration: 'none', fontWeight: 800, padding: '7px 16px', borderRadius: 8 }}>무료 회원가입 →</a>
        </div>
      )}

      <div className="tabs">
        {TABS.map((t) => (
          <button key={t.id} className={tab === t.id ? 'on' : ''} onClick={() => onTab(t.id)}
            title={demo && t.id !== 'list' ? '회원 전용 — 클릭 시 회원가입' : ''}>
            {t.label}{demo && t.id !== 'list' ? ' 🔒' : ''}
          </button>
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

        {!facets ? (
          <div className="panel" style={{ color: '#94a3b8' }}>데이터 불러오는 중…</div>
        ) : (
          <>
            {tab === 'list' && <ProfitList facets={facets} month={month} demo={demo} onSignup={goSignup} />}
            {tab === 'rank' && <RankTab month={month} />}
            {tab === 'reco' && <RecoTab month={month} />}
          </>
        )}
      </div>
    </>
  )
}
