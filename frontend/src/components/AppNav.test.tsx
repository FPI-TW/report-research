import { test, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AppNav } from './AppNav'

// AccountArea 讀 /api/stats.username；mock 只需回 username（AppNav 僅用 getStats）。
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
          <AppNav />
        </MantineProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

test('渲染品牌與三個導覽連結（正確 href）', () => {
  wrap('/app/search')
  expect(screen.getByText('廷豐智能研報')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '搜尋' })).toHaveAttribute('href', '/app/search')
  expect(screen.getByRole('link', { name: '問答' })).toHaveAttribute('href', '/app/ask')
  expect(screen.getByRole('link', { name: '監控' })).toHaveAttribute('href', '/app/monitor')
})

test('主導覽為具名 landmark', () => {
  wrap('/app/search')
  expect(screen.getByRole('navigation', { name: '主導覽' })).toBeInTheDocument()
})

test('目前分路的連結帶 aria-current=page，其餘無', () => {
  wrap('/app/ask')
  expect(screen.getByRole('link', { name: '問答' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('link', { name: '搜尋' })).not.toHaveAttribute('aria-current', 'page')
})

test('帳號區：頭像 + 登出（原生 form POST /logout）', () => {
  wrap('/app/search')
  expect(screen.getByTestId('account-avatar')).toHaveAttribute('src', '/static/img/avatar.jpg')
  const logout = screen.getByRole('button', { name: '登出' })
  expect(logout).toHaveAttribute('type', 'submit')
  const form = logout.closest('form')
  expect(form).toHaveAttribute('action', '/logout')
  expect(form).toHaveAttribute('method', 'post')
})

test('帳號名由 /api/stats.username 渲染', async () => {
  wrap('/app/search')
  expect(await screen.findByText('研究員')).toBeInTheDocument()
})
