// 페이지네이션. 기존 renderPager 로직(양끝+현재 주변만, 나머지 …)을 그대로 옮김.
export default function Pager({ page, pages, onGo }) {
  if (pages <= 1) return null
  const want = [...new Set([1, 2, page - 1, page, page + 1, pages - 1, pages])]
    .filter((p) => p >= 1 && p <= pages)
    .sort((a, b) => a - b)

  const items = []
  items.push(
    <button key="prev" disabled={page === 1} onClick={() => onGo(page - 1)}>‹</button>,
  )
  let prev = 0
  want.forEach((p) => {
    if (p - prev > 1) items.push(<span className="ell" key={`e${p}`}>…</span>)
    items.push(
      <button key={p} className={p === page ? 'on' : ''} onClick={() => onGo(p)}>{p}</button>,
    )
    prev = p
  })
  items.push(
    <button key="next" disabled={page === pages} onClick={() => onGo(page + 1)}>›</button>,
  )
  return <div className="pager">{items}</div>
}
