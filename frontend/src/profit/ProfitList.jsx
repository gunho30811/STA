import { useState, useEffect, useCallback } from 'react'
import { fetchProfit, getJSON, qs, readCache, writeCache, fmt as n, shortSido } from '../shared/api.js'
import Detail from './Detail.jsx'
import Pager from './Pager.jsx'

// 매물명 옆 시/도 컬럼 포함(기존 요청 반영). k=API 정렬키, l=좌측정렬, tip=헤더 툴팁 설명.
const COLS = [
  { k: 'name', t: '매물명 / 건물', l: true },
  { k: 'sido', t: '시/도', l: true },
  { k: 'dong', t: '동', l: true },
  { k: 'station', t: '역', l: true, tip: '매물 500m 반경 내 지하철역(없으면 공란)' },
  { k: 'pyeong', t: '평' },
  { k: 'expNet', t: '기대월순수익', tip: '실제 예약률을 반영한 한 달 순수익. 삼삼 단기임대로 돌렸을 때 실제로 손에 남는 돈 — 이 값이 클수록 좋아요(핵심 지표).' },
  { k: 'occ', t: '예약률%', tip: '한 달 중 예약이 찬 비율(수요). 높을수록 실제로 잘 나가는 매물.' },
  { k: 'net', t: '순수익(풀가동)', tip: '예약률 100%를 가정한 이론상 상한. 참고용(실제는 기대월순수익).' },
  { k: 'maxRev', t: '최대수익', tip: '풀가동(100% 예약) 시 한 달 매출(주당×4.345).' },
]

const DEFAULTS = {
  sido: '', sigungu: '', dong: '', btype: '', rooms: '',
  station: '', keyword: '', matches_min: '', net_min: '', maxrev_min: '',
  occ_min: '20', dongocc_min: '', dep_max: '', pyeong_min: '', pyeong_max: '',
}

export default function ProfitList({ facets, month, demo = false, onSignup }) {
  const [f, setF] = useState(DEFAULTS)
  const [sort, setSort] = useState({ col: 'expNet', dir: 'desc' })
  const [res, setRes] = useState({ items: [], summary: {}, total: 0, page: 1, pages: 1 })
  const [sel, setSel] = useState(null)
  const [showAdv, setShowAdv] = useState(false)   // 상세 옵션 접기(핵심만 먼저 보여주려고)
  const [term, setTerm] = useState(null)          // 용어 설명 팝업({t, tip}) — ⓘ/헤더 눌러서
  const [showTerms, setShowTerms] = useState(false)  // 전체 용어 설명(모바일 카드엔 헤더가 없어서)

  const tree = facets.tree || {}
  const upd = (k, v) => setF((s) => ({ ...s, [k]: v }))

  const doSearch = useCallback(async (page, sortOverride) => {
    const srt = sortOverride || sort
    const params = { ...f, month, sort: srt.col, dir: srt.dir, page, size: 40 }
    const path = 'api/profit?' + qs(params).toString()
    // 기본 화면(첫 진입 조건)은 localStorage에 저장해뒀다가 재방문 때 즉시 표시 → 그 다음 최신 갱신.
    const isDefault = page === 1 && !sortOverride && JSON.stringify(f) === JSON.stringify(DEFAULTS)
    if (isDefault) {
      const cached = readCache(path)
      if (cached) setRes(cached)   // 네트워크 대기 없이 바로
    }
    const data = await getJSON(path)
    if (isDefault) writeCache(path, data)
    setRes(data)
  }, [f, month, sort])

  // 최초 + 기준월 변경 시 재조회
  useEffect(() => { doSearch(1) /* eslint-disable-next-line */ }, [month])

  const onSort = (k) => {
    const dir = sort.col === k ? (sort.dir === 'asc' ? 'desc' : 'asc') : 'desc'
    const next = { col: k, dir }
    setSort(next)
    doSearch(1, next)
  }

  // 새 조건으로 검색하면 이전에 클릭해 둔 매물 상세는 초기화(QA #3). 정렬·페이징은 유지.
  const runSearch = () => {
    setSel(null)
    doSearch(1)
  }

  const reset = () => {
    setF(DEFAULTS)
    setSort({ col: 'expNet', dir: 'desc' })
    setSel(null)
    // DEFAULTS로 즉시 조회
    fetchProfit({ ...DEFAULTS, month, sort: 'expNet', dir: 'desc', page: 1, size: 40 }).then(setRes)
  }

  // 시/도 → 시군구 → 동 종속 드롭다운
  const sigungus = f.sido && tree[f.sido] ? Object.keys(tree[f.sido]).sort() : []
  const dongs = f.sido && f.sigungu && tree[f.sido]?.[f.sigungu] ? tree[f.sido][f.sigungu] : []

  const s = res.summary || {}

  return (
    <div>
      <div className="cards">
        <Card lbl="매물 수" val={(s.count ?? 0).toLocaleString()} />
        <Card lbl="순수익 중앙값" val={<>{n(s.net_med)}<small> 만원</small></>} />
        <Card lbl="순수익 최대" val={<>{n(s.net_max)}<small> 만원</small></>} cls="pos" />
        <Card lbl="예약률 중앙값" val={<>{n(s.occ_med)}<small> %</small></>} cls="occ" />
      </div>

      {demo ? (
        <div className="panel" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', background: '#f8fafc' }}>
          <span style={{ fontSize: 13.5, color: '#475569' }}>🔒 지역·건물유형·순수익·평수 등 <b>상세 필터는 회원 전용</b>이에요.</span>
          <button className="btn btn-go" onClick={onSignup}>회원가입하고 필터 쓰기 →</button>
        </div>
      ) : (
      <div className="panel">
        {/* 핵심 필터 — 대부분은 지역·방수·예약률만 있으면 충분 */}
        <div className="filters">
          <Sel label="시/도" value={f.sido} onChange={(v) => setF((st) => ({ ...st, sido: v, sigungu: '', dong: '' }))} opts={facets.sido || []} />
          <Sel label="방수" value={f.rooms} onChange={(v) => upd('rooms', v)} opts={['원룸', '투룸', '쓰리룸+']} />
          <Num label="예약률 ≥ (기본 20%)" value={f.occ_min} onChange={(v) => upd('occ_min', v)} ph="%" />
          <div className="fg"><label>&nbsp;</label><button className="btn btn-go" onClick={runSearch}>검색</button></div>
          <div className="fg"><label>&nbsp;</label><button className="btn btn-reset" onClick={reset}>초기화</button></div>
          <div className="fg"><label>&nbsp;</label>
            <button className="btn" style={{ background: '#eef2ff', color: '#3730a3' }} onClick={() => setShowAdv((s) => !s)}>
              🔧 상세 옵션 {showAdv ? '▲' : '▾'}
            </button>
          </div>
        </div>

        {/* 상세 옵션 — 필요할 때만 펼쳐서(지역 세부·건물유형·역·금액·평수 등) */}
        {showAdv && (
          <div className="filters" style={{ marginTop: 12, paddingTop: 12, borderTop: '1px dashed #e5e7eb' }}>
            <Sel label="시군구" value={f.sigungu} onChange={(v) => setF((st) => ({ ...st, sigungu: v, dong: '' }))} opts={sigungus} />
            <Sel label="동" value={f.dong} onChange={(v) => upd('dong', v)} opts={dongs} />
            <Sel label="건물유형" value={f.btype} onChange={(v) => upd('btype', v)} opts={facets.btype || []} />
            <Txt label="🚇 역 검색" value={f.station} onChange={(v) => upd('station', v)} ph="예: 강남" onEnter={runSearch} />
            <Txt label="키워드" value={f.keyword} onChange={(v) => upd('keyword', v)} ph="매물/건물" onEnter={runSearch} />
            <Num label="매칭수 ≥" value={f.matches_min} onChange={(v) => upd('matches_min', v)} ph="개" />
            <Num label="순수익 ≥" value={f.net_min} onChange={(v) => upd('net_min', v)} ph="만원" />
            <Num label="최대수익 ≥" value={f.maxrev_min} onChange={(v) => upd('maxrev_min', v)} ph="만원" />
            <Num label="동예약률 ≥" value={f.dongocc_min} onChange={(v) => upd('dongocc_min', v)} ph="%" />
            <Num label="보증금 ≤" value={f.dep_max} onChange={(v) => upd('dep_max', v)} ph="만원" />
            <div className="fg">
              <label>평수</label>
              <div style={{ display: 'flex', gap: 4 }}>
                <input type="number" value={f.pyeong_min} onChange={(e) => upd('pyeong_min', e.target.value)} placeholder="최소" style={{ width: 60 }} />
                <input type="number" value={f.pyeong_max} onChange={(e) => upd('pyeong_max', e.target.value)} placeholder="최대" style={{ width: 60 }} />
              </div>
            </div>
            <div className="fg"><label>&nbsp;</label><button className="btn btn-go" onClick={runSearch}>적용</button></div>
          </div>
        )}
        <div className="legend warn">
          ⚠️ 기본값으로 <b>예약률 20% 이상</b>만 보여줘요(예약 0%인 이론값 매물 제외). 전체는 예약률 칸을 비우고 검색.
        </div>
      </div>
      )}

      {/* 첫 진입 안내: 무엇을·어떤 순서로 + 용어는 눌러서(모바일 터치 대응) */}
      <div style={{ background: '#eff6ff', border: '1px solid #dbeafe', borderRadius: 9, padding: '9px 13px', margin: '0 0 10px', fontSize: 13, color: '#1e3a5f', lineHeight: 1.55 }}>
        💡 <b>단기임대로 돌리면 한 달에 얼마 남나(기대월순수익)</b> 높은 순이에요.{' '}
        <b role="button" style={{ color: '#2563eb', cursor: 'pointer', textDecoration: 'underline' }} onClick={() => setShowTerms(true)}>용어 설명 보기</b>
        {demo ? '' : ' · 매물을 누르면 상세'}.
      </div>

      <div className="md">
        <div className="listcol">
          <table>
            <thead>
              <tr>
                {COLS.map((c) => (
                  <th key={c.k} className={`col-${c.k}${c.l ? ' l' : ''}`} onClick={() => onSort(c.k)}>
                    {c.t}
                    {c.tip && (
                      <span role="button" title="설명 보기" style={{ color: '#2563eb', fontWeight: 700, cursor: 'help' }}
                        onClick={(e) => { e.stopPropagation(); setTerm({ t: c.t, tip: c.tip }) }}> ⓘ</span>
                    )}
                    {c.k === sort.col ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {res.items.length === 0 ? (
                <tr><td colSpan={COLS.length} className="d-empty">조건에 맞는 매물 없음</td></tr>
              ) : res.items.map((x) => {
                const ec = x.expNet != null && x.expNet >= 0 ? 'pos' : 'neg'
                const zeroOcc = x.occ == null || x.occ === 0
                return (
                  <tr key={x.id} className={x.id === sel?.id ? 'on' : ''} onClick={() => (demo ? onSignup() : setSel(x))}
                    title={demo ? '상세는 회원 전용 — 클릭 시 회원가입' : ''}>
                    <td className="l col-name"><span className="name">{x.name || ''}</span><br /><span className="sub">{x.bldg || ''}</span></td>
                    <td className="l col-sido">{x.sido || ''}</td>
                    <td className="l col-dong">{x.dong || ''}</td>
                    <td className="l col-station">{x.station || '-'}</td>
                    <td className="col-pyeong">{n(x.pyeong)}</td>
                    <td className={`col-expNet ${ec}`} style={{ fontWeight: 800 }}>{n(x.expNet)}</td>
                    <td className={`col-occ ${zeroOcc ? '' : 'occ'}`} style={zeroOcc ? { color: '#dc2626', fontWeight: 700 } : undefined}>{n(x.occ)}%</td>
                    <td className="col-net" style={{ color: '#94a3b8' }}>{n(x.net)}</td>
                    <td className="col-maxRev" style={{ color: '#94a3b8' }}>{n(x.maxRev)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          {/* 모바일 전용 카드 목록(표는 좁은 화면에서 잘려 CSS로 숨김). 매물명 전체·시·동·역이 다 보임 */}
          <div className="listcards">
            {res.items.length === 0 ? (
              <div className="d-empty">조건에 맞는 매물 없음</div>
            ) : res.items.map((x) => (
              <MatchCard key={x.id} x={x} onClick={() => (demo ? onSignup() : setSel(x))} />
            ))}
          </div>
        </div>
        {!demo && <Detail item={sel} onClose={() => setSel(null)} />}
      </div>
      {demo ? (
        <div className="panel" style={{ textAlign: 'center', padding: '24px 16px', background: 'linear-gradient(180deg,#fff,#f1f5f9)' }}>
          <div style={{ fontSize: 15, fontWeight: 800, color: '#1e293b' }}>🔒 {n(res.locked)}개 매물이 더 있어요</div>
          <div style={{ fontSize: 13, color: '#64748b', margin: '6px 0 14px' }}>회원가입하면 전체 매물 · 지역/순수익 필터 · 상세 · 순위 · 신규진입 추천까지 전부 무료로.</div>
          <button className="btn btn-go" style={{ padding: '12px 26px', fontSize: 15 }} onClick={onSignup}>무료 회원가입하고 전체 보기 →</button>
        </div>
      ) : (
        <Pager page={res.page} pages={res.pages} onGo={(p) => doSearch(p)} />
      )}

      {/* 용어 설명 팝업 — 헤더 ⓘ를 눌러서(단일). 바깥/확인 눌러 닫기 */}
      {term && (
        <div className="term-overlay" onClick={() => setTerm(null)}>
          <div className="term-pop" onClick={(e) => e.stopPropagation()}>
            <div className="term-t">{term.t}</div>
            <div className="term-d">{term.tip}</div>
            <button className="btn btn-go" style={{ marginTop: 12 }} onClick={() => setTerm(null)}>확인</button>
          </div>
        </div>
      )}

      {/* 전체 용어 설명 — 안내 배너의 '용어 설명 보기'(모바일 카드엔 헤더가 없어서) */}
      {showTerms && (
        <div className="term-overlay" onClick={() => setShowTerms(false)}>
          <div className="term-pop" style={{ maxWidth: 380, textAlign: 'left' }} onClick={(e) => e.stopPropagation()}>
            <div className="term-t" style={{ textAlign: 'center' }}>용어 설명</div>
            <div style={{ marginTop: 10 }}>
              {COLS.filter((c) => c.tip).map((c) => (
                <div key={c.k} style={{ padding: '8px 0', borderBottom: '1px solid #eef0f2' }}>
                  <div style={{ fontWeight: 800, fontSize: 13.5, color: '#111827' }}>{c.t}</div>
                  <div style={{ fontSize: 12.5, color: '#475569', lineHeight: 1.5, marginTop: 2 }}>{c.tip}</div>
                </div>
              ))}
            </div>
            <button className="btn btn-go" style={{ marginTop: 14, width: '100%' }} onClick={() => setShowTerms(false)}>확인</button>
          </div>
        </div>
      )}
    </div>
  )
}

function Card({ lbl, val, cls }) {
  return <div className="card"><div className="lbl">{lbl}</div><div className={`val ${cls || ''}`}>{val}</div></div>
}

// 모바일 매물 카드 — 매물명 전체 + 지역(시·동·역·평) + 기대월순수익/예약률.
function MatchCard({ x, onClick }) {
  const ec = x.expNet != null && x.expNet >= 0 ? 'pos' : 'neg'
  const zeroOcc = x.occ == null || x.occ === 0
  const area = [shortSido(x.sido), x.dong, x.station].filter(Boolean).join(' · ')
  return (
    <div className="mcard" onClick={onClick}>
      <div className="mc-main">
        <div className="mc-name">{x.name || ''}</div>
        {x.bldg && <div className="mc-bldg">{x.bldg}</div>}
        <div className="mc-area">{area}{x.pyeong ? ` · ${n(x.pyeong)}평` : ''}</div>
      </div>
      <div className="mc-nums">
        <div className={`mc-net ${ec}`}>{n(x.expNet)}<small>만</small></div>
        <div className={`mc-occ ${zeroOcc ? 'zero' : ''}`}>예약 {n(x.occ)}%</div>
      </div>
    </div>
  )
}
function Sel({ label, value, onChange, opts }) {
  return (
    <div className="fg"><label>{label}</label>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">전체</option>
        {opts.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  )
}
function Txt({ label, value, onChange, ph, onEnter }) {
  return (
    <div className="fg"><label>{label}</label>
      <input value={value} placeholder={ph} onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && onEnter) onEnter() }} />
    </div>
  )
}
function Num({ label, value, onChange, ph }) {
  return (
    <div className="fg"><label>{label}</label>
      <input type="number" value={value} placeholder={ph} onChange={(e) => onChange(e.target.value)} />
    </div>
  )
}
