import React from 'react'
import ReactDOM from 'react-dom/client'

// 어떤 뷰어를 렌더할지는 빌드/실행 시 VIEWER 환경변수로 결정(vite.config의 define가 주입).
// VITE_VIEWER가 빌드 상수라 아래 분기는 상수 폴딩돼, 각 빌드엔 해당 뷰어만 번들된다.
const viewer = import.meta.env.VITE_VIEWER || 'profit'

const load = viewer === 'gangnam'
  ? () => import('./gangnam/App.jsx')
  : () => import('./profit/App.jsx')

load().then(({ default: App }) => {
  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  )
})
