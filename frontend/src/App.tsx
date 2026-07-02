import { Suspense, lazy } from 'react'
import { createBrowserRouter, Navigate, Outlet } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { AppShell, Loader, Title, VisuallyHidden } from '@mantine/core'
import { useMediaQuery } from '@mantine/hooks'
import { AppRail } from './components/AppRail'
import { MobileTabBar } from './components/MobileTabBar'
import { MOBILE_NAV_QUERY } from './components/navLinks'

const MonitorPage = lazy(() => import('./features/monitor/MonitorPage'))
const SearchPage = lazy(() => import('./features/search/SearchPage'))
const AskPage = lazy(() => import('./features/ask/AskPage'))

function RootLayout() {
  // 手機（<=48em）：底部分頁列；桌機：左側窄軌。jsdom 無 matchMedia → 預設桌機。
  const isMobile = useMediaQuery(MOBILE_NAV_QUERY, false, { getInitialValueInEffect: false })
  return (
    <AppShell
      navbar={isMobile ? undefined : { width: 60, breakpoint: 0 }}
      footer={isMobile ? { height: 56 } : undefined}
      padding="md"
    >
      {!isMobile && (
        <AppShell.Navbar>
          <AppRail />
        </AppShell.Navbar>
      )}
      <AppShell.Main>
        {/* 全站唯一 h1（視覺隱藏）：確保每頁有正常的 h1→h2 標題梯級 */}
        <VisuallyHidden component="h1">廷豐智能研報</VisuallyHidden>
        <Outlet />
      </AppShell.Main>
      {isMobile && (
        <AppShell.Footer>
          <MobileTabBar />
        </AppShell.Footer>
      )}
    </AppShell>
  )
}

function NotFound() {
  return <Title order={3}>找不到頁面</Title>
}

function RouteFallback() {
  return <Loader />
}

// router 建在模組層（render 樹之外），basename 無尾斜線。
// eslint-disable-next-line react-refresh/only-export-components
export const routes = [
  {
    element: <RootLayout />,
    children: [
      { path: '/', element: <Navigate to="/search" replace /> },
      {
        path: '/monitor',
        element: (
          <Suspense fallback={<RouteFallback />}>
            <MonitorPage />
          </Suspense>
        ),
      },
      {
        path: '/search',
        element: (
          <Suspense fallback={<RouteFallback />}>
            <SearchPage />
          </Suspense>
        ),
      },
      {
        path: '/ask',
        element: (
          <Suspense fallback={<RouteFallback />}>
            <AskPage />
          </Suspense>
        ),
      },
      { path: '*', element: <NotFound /> },
    ],
  },
]

const router = createBrowserRouter(routes, { basename: '/app' })

export default function App() {
  return <RouterProvider router={router} />
}
