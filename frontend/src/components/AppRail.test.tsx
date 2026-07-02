import { test, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AppRail } from './AppRail'

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
          <AppRail />
        </MantineProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

test('品牌「廷」連到 /app/search，帶完整品牌名 aria-label', () => {
  wrap('/app/search')
  const brand = screen.getByRole('link', { name: '廷豐智能研報' })
  expect(brand).toHaveAttribute('href', '/app/search')
  expect(brand).toHaveTextContent('廷')
})

test('主導覽 landmark 含三個連結（正確 href）', () => {
  wrap('/app/search')
  expect(screen.getByRole('navigation', { name: '主導覽' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '搜尋' })).toHaveAttribute('href', '/app/search')
  expect(screen.getByRole('link', { name: '問答' })).toHaveAttribute('href', '/app/ask')
  expect(screen.getByRole('link', { name: '監控' })).toHaveAttribute('href', '/app/monitor')
})

test('目前分路帶 aria-current=page，其餘無', () => {
  wrap('/app/ask')
  expect(screen.getByRole('link', { name: '問答' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('link', { name: '搜尋' })).not.toHaveAttribute('aria-current', 'page')
})

test('左軌含帳號選單（頭像鈕）', () => {
  wrap('/app/search')
  expect(screen.getByTestId('account-avatar')).toBeInTheDocument()
})
