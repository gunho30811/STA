// 공용 필터 패널: 시/도 복수선택(칩) + 시군구·동(종속) + 건물유형 + 평수·주당 범위 + 분석 버튼.
// 기존 samsam.html 상단 필터 이식. 값은 상위(App)의 filter 상태를 그대로 쓴다.
export default function FilterPanel({ facets, f, setF, onRun }) {
  const tree = facets.tree || {}
  const srcSidos = f.sidos.length ? f.sidos : Object.keys(tree)

  const gus = [...new Set(srcSidos.flatMap((s) => (tree[s] ? Object.keys(tree[s]) : [])))].sort()
  const dongs = f.sigungu
    ? [...new Set(srcSidos.flatMap((s) => (tree[s]?.[f.sigungu]) || []))].sort()
    : []

  const toggleSido = (s) => {
    const sidos = f.sidos.includes(s) ? f.sidos.filter((x) => x !== s) : [...f.sidos, s]
    setF({ ...f, sidos, sigungu: '', dong: '' })   // 시/도 바뀌면 시군구·동 초기화
  }

  return (
    <div className="panel">
      <div className="filters">
        <div className="fg">
          <label>시/도 <span className="mut" style={{ fontWeight: 400 }}>(복수선택)</span></label>
          <div className="chips">
            {facets.sido.map((s) => (
              <label key={s} className={f.sidos.includes(s) ? 'on' : ''}>
                <input type="checkbox" checked={f.sidos.includes(s)} onChange={() => toggleSido(s)} />{s}
              </label>
            ))}
          </div>
        </div>
        <div className="fg"><label>시군구</label>
          <select value={f.sigungu} onChange={(e) => setF({ ...f, sigungu: e.target.value, dong: '' })}>
            <option value="">전체</option>{gus.map((g) => <option key={g}>{g}</option>)}
          </select>
        </div>
        <div className="fg"><label>동</label>
          <select value={f.dong} onChange={(e) => setF({ ...f, dong: e.target.value })}>
            <option value="">전체</option>{dongs.map((d) => <option key={d}>{d}</option>)}
          </select>
        </div>
        <div className="fg"><label>건물유형</label>
          <select value={f.btype} onChange={(e) => setF({ ...f, btype: e.target.value })}>
            <option value="">전체</option>{facets.building_type.map((b) => <option key={b}>{b}</option>)}
          </select>
        </div>
        <div className="fg"><label>평수</label>
          <div style={{ display: 'flex', gap: 4 }}>
            <input type="number" value={f.pmin} placeholder="최소" style={{ width: 64 }} onChange={(e) => setF({ ...f, pmin: e.target.value })} />
            <input type="number" value={f.pmax} placeholder="최대" style={{ width: 64 }} onChange={(e) => setF({ ...f, pmax: e.target.value })} />
          </div>
        </div>
        <div className="fg"><label>주당(만원)</label>
          <div style={{ display: 'flex', gap: 4 }}>
            <input type="number" value={f.wmin} placeholder="최소" style={{ width: 64 }} onChange={(e) => setF({ ...f, wmin: e.target.value })} />
            <input type="number" value={f.wmax} placeholder="최대" style={{ width: 64 }} onChange={(e) => setF({ ...f, wmax: e.target.value })} />
          </div>
        </div>
        <div className="fg"><label>&nbsp;</label><button className="btn btn-go" onClick={onRun}>분석</button></div>
      </div>
    </div>
  )
}
