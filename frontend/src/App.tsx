import { Suspense, lazy } from 'react'
import { createBrowserRouter, Navigate } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { AppShell } from './components/shell/AppShell'

const SearchPage = lazy(() => import('./features/search/SearchPage'))
const AskPage = lazy(() => import('./features/ask/AskPage'))
const MonitorPage = lazy(() => import('./features/monitor/MonitorPage'))

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
      { path: '*', element: <NotFound /> },
    ],
  },
]

const router = createBrowserRouter(routes, { basename: '/app' })

export default function App() {
  return <RouterProvider router={router} />
}
