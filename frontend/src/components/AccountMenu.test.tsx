import { test, expect, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AccountMenu } from './AccountMenu'

// AccountMenu 讀 /api/stats.username；mock 只需回 username。
vi.mock('../features/search/api', () => ({
  getStats: vi.fn(async () => ({
    total_reports: 0,
    markets: [],
    instrument_types: [],
    report_types: [],
    username: '研究員',
  })),
}))

const wrap = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MantineProvider>
        <AccountMenu />
      </MantineProvider>
    </QueryClientProvider>,
  )
}

test('頭像鈕存在（data-testid 平價保留）', () => {
  wrap()
  expect(screen.getByTestId('account-avatar').getAttribute('src')).toMatch(/avatar\.jpg$/)
  expect(screen.getByRole('button', { name: '帳號選單' })).toBeInTheDocument()
})

test('點開選單：帳號名 + 登出（原生 form POST /logout）', async () => {
  wrap()
  fireEvent.click(screen.getByRole('button', { name: '帳號選單' }))
  expect(await screen.findByText('研究員')).toBeInTheDocument()
  const logoutText = await screen.findByText('登出')
  const logout = logoutText.closest('button')
  expect(logout).toHaveAttribute('type', 'submit')
  const form = logout.closest('form')
  expect(form).toHaveAttribute('action', '/logout')
  expect(form).toHaveAttribute('method', 'post')
})

test('username 空白時顯示預設「使用者」', async () => {
  const api = await import('../features/search/api')
  vi.mocked(api.getStats).mockResolvedValueOnce({
    total_reports: 0,
    markets: [],
    instrument_types: [],
    report_types: [],
    username: '  ',
  } as never)
  wrap()
  fireEvent.click(screen.getByRole('button', { name: '帳號選單' }))
  expect(await screen.findByText('使用者')).toBeInTheDocument()
})
