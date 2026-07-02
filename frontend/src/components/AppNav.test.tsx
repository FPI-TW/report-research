import { afterEach, test, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
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

afterEach(() => {
  vi.unstubAllGlobals()
})

function mockMatchMedia(matchingQueries: ReadonlyArray<string>) {
  vi.stubGlobal('matchMedia', vi.fn().mockImplementation((query: string) => ({
    matches: matchingQueries.includes(query),
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })))
}

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
  expect(screen.getByTestId('account-avatar').getAttribute('src')).toMatch(/avatar\.jpg$/)
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

test('手機寬度時隱藏帳號名避免導覽擠壓', async () => {
  mockMatchMedia(['(max-width: 40em)'])
  wrap('/app/search')
  expect(await screen.findByTestId('account-avatar')).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByText('研究員')).toBeNull())
})

test('窄手機寬度時隱藏頭像與帳號名，保留主要導覽', async () => {
  mockMatchMedia(['(max-width: 40em)', '(max-width: 23em)'])
  wrap('/app/search')
  await waitFor(() => expect(screen.queryByText('研究員')).toBeNull())
  expect(screen.queryByTestId('account-avatar')).toBeNull()
  expect(screen.getByRole('link', { name: '搜尋' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '登出' })).toBeInTheDocument()
})

test('品牌與導覽文字維持單行不換行', async () => {
  wrap('/app/search')
  expect(screen.getByText('廷豐智能研報')).toHaveStyle({ whiteSpace: 'nowrap' })
  expect(screen.getByText('搜尋')).toHaveStyle({ whiteSpace: 'nowrap' })
  expect(screen.getByText('問答')).toHaveStyle({ whiteSpace: 'nowrap' })
  expect(screen.getByText('監控')).toHaveStyle({ whiteSpace: 'nowrap' })
  expect(await screen.findByText('研究員')).toHaveStyle({ whiteSpace: 'nowrap' })
})
