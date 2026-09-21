import { Suspense, lazy, useEffect } from 'react'
import { createBrowserRouter, Navigate } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { AppShell } from './components/shell/AppShell'
import { RouteError } from './components/shell/RouteError'
import { routeLoaders, preloadIdle } from './lib/routePreload'

const SearchPage = lazy(routeLoaders.search)
const AskPage = lazy(routeLoaders.ask)
const MonitorPage = lazy(routeLoaders.monitor)
const RadarPage = lazy(routeLoaders.radar)
const BriefPage = lazy(routeLoaders.brief)
const HelpPage = lazy(routeLoaders.help)
const ReportPage = lazy(routeLoaders.report)

function NotFound() {
  return <div style={{ padding: 20 }}>找不到頁面</div>
}

// eslint-disable-next-line react-refresh/only-export-components
export const routes = [
  {
    element: <AppShell />,
    // 外層：連外殼都 render 失敗時的最後一道，自己撐滿視窗。
    errorElement: <RouteError standalone />,
    children: [
      {
        // 內層（無 path 的包裝路由）：頁面壞了只換掉內容區，側欄留著讓人切去別頁。
        // 兩層缺一不可——只掛外層的話，任何一頁出錯都會連導覽一起消失。
        errorElement: <RouteError />,
        children: [
          { path: '/', element: <Navigate to="/search" replace /> },
          { path: '/search', element: <Suspense><SearchPage /></Suspense> },
          { path: '/ask', element: <Suspense><AskPage /></Suspense> },
          { path: '/monitor', element: <Suspense><MonitorPage /></Suspense> },
          { path: '/radar', element: <Suspense><RadarPage /></Suspense> },
          { path: '/brief', element: <Suspense><BriefPage /></Suspense> },
          { path: '/help', element: <Suspense><HelpPage /></Suspense> },
          // 研報閱讀頁：從檢索進入的詳情頁，不進導覽列（照 /help 慣例）。須排在 '*' 之前。
          { path: '/report/:hash', element: <Suspense><ReportPage /></Suspense> },
          { path: '*', element: <NotFound /> },
        ],
      },
    ],
  },
]

const router = createBrowserRouter(routes, { basename: '/app' })

export default function App() {
  useEffect(() => { preloadIdle(['search', 'ask']) }, [])
  return <RouterProvider router={router} />
}
