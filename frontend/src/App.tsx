import { Suspense, lazy, useEffect } from 'react'
import { createBrowserRouter, Navigate } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { AppShell } from './components/shell/AppShell'
import { routeLoaders, preloadIdle } from './lib/routePreload'

const SearchPage = lazy(routeLoaders.search)
const AskPage = lazy(routeLoaders.ask)
const MonitorPage = lazy(routeLoaders.monitor)
const RadarPage = lazy(routeLoaders.radar)

function NotFound() {
  return <div style={{ padding: 20 }}>找不到頁面</div>
}

// eslint-disable-next-line react-refresh/only-export-components
export const routes = [
  {
    element: <AppShell />,
    children: [
      { path: '/', element: <Navigate to="/search" replace /> },
      { path: '/search', element: <Suspense><SearchPage /></Suspense> },
      { path: '/ask', element: <Suspense><AskPage /></Suspense> },
      { path: '/monitor', element: <Suspense><MonitorPage /></Suspense> },
      { path: '/radar', element: <Suspense><RadarPage /></Suspense> },
      { path: '*', element: <NotFound /> },
    ],
  },
]

const router = createBrowserRouter(routes, { basename: '/app' })

export default function App() {
  useEffect(() => { preloadIdle(['search', 'ask']) }, [])
  return <RouterProvider router={router} />
}
