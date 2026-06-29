import type { ReactNode } from 'react'
import { createBrowserRouter } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { AppShell, Container, Title, Text } from '@mantine/core'
import MonitorPage from './features/monitor/MonitorPage'

function Layout({ children }: { children: ReactNode }) {
  return (
    <AppShell padding="md">
      <AppShell.Main>
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

// router 建在模組層（render 樹之外），basename 無尾斜線。
const router = createBrowserRouter(
  [
    { path: '/', element: <Home /> },
    { path: '/monitor', element: <Layout><MonitorPage /></Layout> },
    { path: '*', element: <NotFound /> },
  ],
  { basename: '/app' },
)

export default function App() {
  return <RouterProvider router={router} />
}
