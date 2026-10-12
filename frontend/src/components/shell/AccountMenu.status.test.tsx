import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { AccountMenu } from './AccountMenu'

afterEach(() => vi.unstubAllGlobals())

const STATS = { total_reports: 0, total_chunks: 0, markets: [], instrument_types: [], report_types: [], username: 'analyst' }

/** status：`/api/status` 的回應；null＝500（取不到）。一般使用者身分。 */
function mount(status: { status: string; message: string } | null) {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/me') return new Response(JSON.stringify({ id: 'u1', username: 'analyst', role: 'user' }), { status: 200 })
    if (url === '/api/status') {
      return status ? new Response(JSON.stringify(status), { status: 200 }) : new Response('boom', { status: 500 })
    }
    return new Response(JSON.stringify(STATS), { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={qc}><MemoryRouter><AccountMenu variant="row" /></MemoryRouter></QueryClientProvider>,
  )
  return { fetchMock, view }
}

async function openMenu() {
  expect(await screen.findByText('analyst')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { expanded: false }))
}

test('正常：選單裡顯示「系統狀態：正常」，頭像沒有紅點，也不顯示說明句', async () => {
  const { fetchMock } = mount({ status: 'ok', message: '系統運作正常' })
  await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => u === '/api/status')).toBe(true))
  await openMenu()
  expect(await screen.findByRole('status', { name: '系統狀態：正常' })).toBeInTheDocument()
  expect(screen.queryByText('系統運作正常')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { expanded: true })).toHaveAttribute('title', 'analyst')
})

test('部分異常：頭像 title 提示、選單顯示後端那一句說明；不連到管理後台', async () => {
  mount({ status: 'degraded', message: '部分核心服務異常，檢索或問答可能暫時受影響' })
  await waitFor(() => expect(screen.getByRole('button', { expanded: false })).toHaveAttribute(
    'title', 'analyst（系統狀態：部分異常）'))
  await openMenu()
  expect(screen.getByRole('status', { name: '系統狀態：部分異常' })).toBeInTheDocument()
  expect(screen.getByText('部分核心服務異常，檢索或問答可能暫時受影響')).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: /管理後台/ })).not.toBeInTheDocument()
})

test('狀態未知：照實顯示，不當成異常亮紅點', async () => {
  mount({ status: 'unknown', message: '暫時無法取得完整的系統狀態' })
  await openMenu()
  expect(await screen.findByRole('status', { name: '系統狀態：狀態未知' })).toBeInTheDocument()
  expect(await screen.findByText('暫時無法取得完整的系統狀態')).toBeInTheDocument()
  expect(screen.getByRole('button', { expanded: true })).toHaveAttribute('title', 'analyst')
})

test('端點壞了（500 或格式不符）：退回「狀態未知」，選單照常可用', async () => {
  mount(null)
  await openMenu()
  expect(await screen.findByRole('status', { name: '系統狀態：狀態未知' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '登出' })).toBeInTheDocument()
})
