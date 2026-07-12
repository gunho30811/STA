import React from 'react'
import ReactDOM from 'react-dom/client'

// 어떤 뷰어를 렌더할지는 빌드/실행 시 VIEWER 환경변수로 결정(vite.config의 define가 주입).
// viewer가 빌드 상수라 아래 if 분기는 상수 폴딩돼, 각 빌드엔 해당 뷰어만 번들된다.
const viewer = import.meta.env.VITE_VIEWER || 'profit'

// 4뷰어가 하나의 index.html을 공유해 빌드되므로, 브라우저 탭 제목을 뷰어별로 여기서 지정.
const TITLES = {
  profit: '삼삼 × 부동산 단기임대 수익성',
  gangnam: '수도권 부동산 매물 뷰어',
  samsam: '삼삼 옵션별 공실/예약률 분석',
  chat: '삼삼엠투 통합 채팅',
}
document.title = TITLES[viewer] || TITLES.profit

function loadApp() {
  if (viewer === 'gangnam') return import('./gangnam/App.jsx')
  if (viewer === 'samsam') return import('./samsam/App.jsx')
  if (viewer === 'chat') return import('./chat/App.jsx')
  return import('./profit/App.jsx')
}

loadApp().then(({ default: App }) => {
  const root = ReactDOM.createRoot(document.getElementById('root'))
  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  )
})
