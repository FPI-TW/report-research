import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { SecurityDialog } from './SecurityDialog'

afterEach(() => vi.unstubAllGlobals())

type Reply = { status?: number; body: unknown }

/** 小型假後端：/api/me/totp* 與 /api/me/elevate。 */
function mount(initial: { enabled: boolean }) {
  const state = { enabled: initial.enabled, pending: false, elevated: false }
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(init.body as string) : undefined
    const reply: Reply = (() => {
      if (path === '/api/me') return { body: { id: 'u1', username: 'alice', role: 'user', totp_enabled: state.enabled } }
      if (path === '/api/me/totp') return { body: { enabled: state.enabled, pending: state.pending } }
      if (path === '/api/me/totp/setup') {
        state.pending = true
        return { body: { secret: 'JBSWY3DPEHPK3PXP', otpauth_uri: 'otpauth://totp/x:alice?secret=JBSWY3DPEHPK3PXP' } }
      }
      if (path === '/api/me/totp/confirm') {
        if (body.code !== '123456') return { status: 400, body: { detail: '驗證碼不正確', code: 'bad_totp' } }
        state.enabled = true
        state.pending = false
        return { body: { enabled: true, pending: false } }
      }
      if (path === '/api/me/elevate') {
        if (body.password !== 'alice-password-1' || body.code !== '654321') {
          return { status: 403, body: { detail: '密碼或驗證碼不正確', code: 'bad_password' } }
        }
        state.elevated = true
        return { body: { elevated_until: '2026-10-06T10:10:00Z' } }
      }
      if (path === '/api/me/totp/disable') {
        if (!state.elevated) return { status: 403, body: { detail: '需要重新驗證', code: 'elevation_required' } }
        state.enabled = false
        return { body: { enabled: false, pending: false } }
      }
      return { status: 404, body: { detail: 'not found' } }
    })()
    return new Response(JSON.stringify(reply.body), { status: reply.status ?? 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><SecurityDialog open onClose={() => {}} /></QueryClientProvider>)
  return { fetchMock, state }
}

test('開啟兩步驟驗證：顯示 QR code（不再列出 otpauth 連結文字），可展開手動金鑰，輸入正確驗證碼才啟用', async () => {
  const { state } = mount({ enabled: false })
  const dialog = await screen.findByRole('dialog', { name: '帳號安全' })
  expect(await within(dialog).findByText('未開啟')).toBeInTheDocument()
  fireEvent.click(await within(dialog).findByRole('button', { name: '開啟兩步驟驗證' }))
  const qr = await screen.findByTestId('totp-qr')
  expect(qr).toHaveAttribute('role', 'img')
  expect(qr.querySelector('svg')).not.toBeNull()
  expect(screen.queryByTestId('otpauth-uri')).toBeNull()
  expect(screen.queryByText(/otpauth:\/\//)).toBeNull()
  expect(screen.getByTestId('totp-secret')).toHaveTextContent('JBSWY3DPEHPK3PXP')
  // 這個連結只在窄螢幕顯示（桌面 display:none），以文字定位
  expect(screen.getByText('在這支手機上開啟驗證器').closest('a'))
    .toHaveAttribute('href', 'otpauth://totp/x:alice?secret=JBSWY3DPEHPK3PXP')
  const code = screen.getByLabelText('驗證碼')
  fireEvent.change(code, { target: { value: '000000' } })
  fireEvent.click(screen.getByRole('button', { name: '確認並開啟' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('驗證碼不正確')
  expect(state.enabled).toBe(false)
  fireEvent.change(code, { target: { value: ' 123456 ' } })
  fireEvent.click(screen.getByRole('button', { name: '確認並開啟' }))
  expect(await screen.findByRole('status')).toHaveTextContent('已開啟兩步驟驗證')
  expect(state.enabled).toBe(true)
  expect(await within(dialog).findByText('已開啟')).toBeInTheDocument()
})

test('關閉兩步驟驗證要先重新驗證（密碼＋驗證碼），驗證後自動完成關閉', async () => {
  const { state, fetchMock } = mount({ enabled: true })
  const dialog = await screen.findByRole('dialog', { name: '帳號安全' })
  fireEvent.click(await within(dialog).findByRole('button', { name: '關閉兩步驟驗證' }))
  const elevation = await screen.findByRole('dialog', { name: '重新驗證身分' })
  fireEvent.change(within(elevation).getByLabelText('密碼'), { target: { value: 'alice-password-1' } })
  fireEvent.change(within(elevation).getByLabelText('驗證碼'), { target: { value: '654321' } })
  fireEvent.click(within(elevation).getByRole('button', { name: '驗證' }))
  await waitFor(() => expect(state.enabled).toBe(false))
  expect(await screen.findByRole('status')).toHaveTextContent('已關閉兩步驟驗證')
  const disables = fetchMock.mock.calls.filter(([p]) => p === '/api/me/totp/disable')
  expect(disables).toHaveLength(2) // 第一次 403 elevation_required，驗證後重試
})
