import React from 'react'
import ReactDOM from 'react-dom/client'

// 어떤 뷰어를 렌더할지는 빌드/실행 시 VIEWER 환경변수로 결정(vite.config의 define가 주입).
// viewer가 빌드 상수라 아래 if 분기는 상수 폴딩돼, 각 빌드엔 해당 뷰어만 번들된다.
const viewer = import.meta.env.VITE_VIEWER || 'profit'

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
