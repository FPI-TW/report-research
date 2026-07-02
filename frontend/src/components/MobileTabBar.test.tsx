import { test, expect, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MobileTabBar } from './MobileTabBar'

vi.mock('../features/search/api', () => ({
  getStats: vi.fn(async () => ({
    total_reports: 0,
    markets: [],
    instrument_types: [],
    report_types: [],
    username: '研究員',
  })),
}))

const wrap = (path: string) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[path]} basename="/app">
      <QueryClientProvider client={qc}>
        <MantineProvider>
          <MobileTabBar />
        </MantineProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

test('主導覽 landmark 含三個分頁連結（正確 href）', () => {
  wrap('/app/search')
  expect(screen.getByRole('navigation', { name: '主導覽' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '搜尋' })).toHaveAttribute('href', '/app/search')
  expect(screen.getByRole('link', { name: '問答' })).toHaveAttribute('href', '/app/ask')
  expect(screen.getByRole('link', { name: '監控' })).toHaveAttribute('href', '/app/monitor')
})

test('目前分路帶 aria-current=page', () => {
  wrap('/app/monitor')
  expect(screen.getByRole('link', { name: '監控' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('link', { name: '問答' })).not.toHaveAttribute('aria-current', 'page')
})

test('第 4 格帳號：頭像鈕點開有登出', async () => {
  wrap('/app/search')
  expect(screen.getByText('帳號')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '帳號選單' }))
  expect(await screen.findByText('研究員')).toBeInTheDocument()
})
