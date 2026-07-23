import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { AccountMenu } from './AccountMenu'
import { setLocale } from '../../lib/useLocale'

function stubStats() {
  vi.stubGlobal('fetch', vi.fn(async () =>
    new Response(JSON.stringify({
      total_reports: 0, total_chunks: 0, markets: [], instrument_types: [], report_types: [], username: 'analyst',
    }), { status: 200 }),
  ))
}

beforeEach(() => { localStorage.clear(); setLocale('zh-Hant') })
afterEach(() => { vi.unstubAllGlobals(); setLocale('zh-Hant'); localStorage.clear() })

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}

test('點帳號鈕開選單，顯示登出（原生 form action=/logout）', async () => {
  stubStats()
  wrap(<AccountMenu variant="row" />)
  expect(await screen.findByText('analyst')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { expanded: false }))
  const logout = screen.getByRole('button', { name: '登出' })
  expect(logout).toHaveAttribute('type', 'submit')
  expect(logout.closest('form')).toHaveAttribute('action', '/logout')
  expect(screen.getByRole('link', { name: /使用說明/ })).toHaveAttribute('href', '/help')
})

test('語言切換：預設中文 checked，點 English 切換', async () => {
  stubStats()
  wrap(<AccountMenu variant="row" />)
  expect(await screen.findByText('analyst')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { expanded: false }))
  const zh = screen.getByRole('radio', { name: '中文' })
  const en = screen.getByRole('radio', { name: 'English' })
  expect(zh).toHaveAttribute('aria-checked', 'true')
  expect(en).toHaveAttribute('aria-checked', 'false')
  fireEvent.click(en)
  expect(en).toHaveAttribute('aria-checked', 'true')
  expect(zh).toHaveAttribute('aria-checked', 'false')
  expect(localStorage.getItem('tf.locale')).toBe('en')
})
