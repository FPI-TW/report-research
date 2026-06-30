import { Suspense, lazy, type ReactNode } from 'react'
import { createBrowserRouter } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { AppShell, Container, Loader, Title, Text, VisuallyHidden } from '@mantine/core'

const MonitorPage = lazy(() => import('./features/monitor/MonitorPage'))
const SearchPage = lazy(() => import('./features/search/SearchPage'))
const AskPage = lazy(() => import('./features/ask/AskPage'))

function Layout({ children }: { children: ReactNode }) {
  return (
    <AppShell padding="md">
      <AppShell.Main>
        {/* 全站唯一 h1（視覺隱藏）：確保每頁有正常的 h1→h2 標題梯級 */}
        <VisuallyHidden component="h1">廷豐智能研報</VisuallyHidden>
        <Container size="lg">{children}</Container>
      </AppShell.Main>
    </AppShell>
  )
}

function Home() {
  return (
    <Layout>
      <Title order={2}>廷豐智能研報</Title>
      <Text c="dimmed">SPA 地基已就緒。</Text>
    </Layout>
  )
}

function NotFound() {
  return (
    <Layout>
      <Title order={3}>找不到頁面</Title>
    </Layout>
  )
}

function RouteFallback() {
  return <Loader />
}

// router 建在模組層（render 樹之外），basename 無尾斜線。
const router = createBrowserRouter(
  [
    { path: '/', element: <Home /> },
    {
      path: '/monitor',
      element: (
        <Layout>
          <Suspense fallback={<RouteFallback />}>
            <MonitorPage />
          </Suspense>
        </Layout>
      ),
    },
    {
      path: '/search',
      element: (
        <Layout>
          <Suspense fallback={<RouteFallback />}>
            <SearchPage />
          </Suspense>
        </Layout>
      ),
    },
    {
      path: '/ask',
      element: (
        <Layout>
          <Suspense fallback={<RouteFallback />}>
            <AskPage />
          </Suspense>
        </Layout>
      ),
    },
    { path: '*', element: <NotFound /> },
  ],
  { basename: '/app' },
)

export default function App() {
  return <RouterProvider router={router} />
}
