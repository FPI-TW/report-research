import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { Toaster, dismissToast } from '../../../components/console'
import type { OpsServiceDetail } from '../../../lib/generated/adminApi'
import { OpsServiceActions } from './OpsServiceActions'

afterEach(() => {
  vi.unstubAllGlobals()
  dismissToast()
})

const service = (name: string, actions: OpsServiceDetail['actions']) => ({
  name, kind: 'systemd', tier: 'critical', target: `report-mark-${name}.service`, timer: null, actions,
  description: '', summary: 'running', error: null, systemd: null, container: null, timer_state: null,
  checked_at: '2026-10-06T02:00:00Z',
}) as unknown as OpsServiceDetail

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const ACCEPTED = {
  name: 'web', kind: 'systemd', tier: 'critical', target: 'report-mark-web.service', timer: null,
  actions: ['status', 'logs', 'restart'], group: 'web', description: '',
  action: 'restart', state: 'scheduled', execute_after_ms: 1500,
  accepted_at: '2026-10-06T02:00:00Z', checked_at: '2026-10-06T02:00:00Z',
}

const OPERATE = ['admin', 'ops.read', 'ops.operate']
/** 還在重新驗證的 10 分鐘視窗內（`/api/me` 的 elevated_until 在未來）：操作不會再跳驗證框。 */
const ELEVATED = { elevated_until: '2099-01-01T00:00:00Z' }

/** scopes：/api/me 回的實際 scope；me：/api/me 的其他欄位；handlers：依 URL＋method 回應，其餘 404。 */
function stub(scopes: string[], handlers: Record<string, (init?: RequestInit) => Response>, me: Record<string, unknown> = {}) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/me') {
      return json({ id: 'u1', username: 'ops', role: 'admin', is_super: false, scopes, totp_enabled: false, ...me })
    }
    const h = handlers[`${init?.method ?? 'GET'} ${url}`]
    return h ? h(init) : json({ detail: 'not found', code: 'not_found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function wrap(svc: OpsServiceDetail) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><OpsServiceActions service={svc} /><Toaster /></QueryClientProvider>)
}

const calls = (m: ReturnType<typeof stub>, key: string) =>
  m.mock.calls.filter(([url, init]) => `${(init as RequestInit | undefined)?.method ?? 'GET'} ${url}` === key).length

test('沒有 ops.operate 的管理員看不到任何操作按鈕', async () => {
  const m = stub(['admin', 'ops.read'], {})
  wrap(service('web', ['status', 'logs', 'restart']))
  await waitFor(() => expect(calls(m, 'GET /api/me')).toBeGreaterThan(0))
  await waitFor(() => expect(screen.queryByRole('button', { name: '重新啟動' })).not.toBeInTheDocument())
  expect(screen.queryByLabelText('服務操作')).not.toBeInTheDocument()
})

test('catalog 沒開放寫入的服務（唯讀）不顯示按鈕', async () => {
  stub(OPERATE, {})
  wrap(service('postgres', ['status', 'logs']))
  await new Promise((r) => setTimeout(r, 20))
  expect(screen.queryByRole('button', { name: /重新啟動|立即執行/ })).not.toBeInTheDocument()
})

test('有 ops.operate：底列說明需要的權限與重新驗證，按鈕照 catalog 開放的操作', async () => {
  stub(OPERATE, {})
  wrap(service('web', ['status', 'logs', 'restart']))
  const group = within(await screen.findByRole('group', { name: '服務操作' }))
  expect(group.getByRole('button', { name: '重新啟動' })).toBeInTheDocument()
  expect(group.queryByRole('button', { name: '立即執行' })).not.toBeInTheDocument()
  expect(screen.getByText('需要 ops.operate，執行前會要求重新驗證')).toBeInTheDocument()
})

test('重新啟動要先確認；取消不送出', async () => {
  const m = stub(OPERATE, { 'POST /api/admin/ops/services/web/restart': () => json(ACCEPTED, 202) }, ELEVATED)
  wrap(service('web', ['status', 'logs', 'restart']))
  fireEvent.click(await screen.findByRole('button', { name: '重新啟動' }))
  const confirm = within(screen.getByRole('alertdialog', { name: '重新啟動 web' }))
  expect(confirm.getByText(/確定要重新啟動「web」/)).toHaveTextContent('重啟期間服務會短暫中斷')
  fireEvent.click(confirm.getByRole('button', { name: '取消' }))
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  expect(calls(m, 'POST /api/admin/ops/services/web/restart')).toBe(0)
})

test('確認後送出重新啟動，toast 顯示已排程', async () => {
  const m = stub(OPERATE, { 'POST /api/admin/ops/services/web/restart': () => json(ACCEPTED, 202) }, ELEVATED)
  wrap(service('web', ['status', 'logs', 'restart']))
  fireEvent.click(await screen.findByRole('button', { name: '重新啟動' }))
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '重新啟動' }))
  expect(await screen.findByRole('status')).toHaveTextContent('已排程重新啟動 web，約 1.5 秒後執行')
  expect(calls(m, 'POST /api/admin/ops/services/web/restart')).toBe(1)
  // 還在驗證視窗內：不再詢問
  expect(calls(m, 'POST /api/me/elevate')).toBe(0)
})

test('不在驗證視窗內：確認後先重新驗證身分，驗證成功才送出', async () => {
  const m = stub(OPERATE, {
    'POST /api/admin/ops/services/web/restart': () => json(ACCEPTED, 202),
    'POST /api/me/elevate': () => json(ELEVATED),
  })
  wrap(service('web', ['status', 'logs', 'restart']))
  fireEvent.click(await screen.findByRole('button', { name: '重新啟動' }))
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '重新啟動' }))
  const dialog = await screen.findByRole('dialog', { name: '重新驗證身分' })
  expect(dialog).toHaveTextContent('「重新啟動 web」需要重新驗證')
  expect(calls(m, 'POST /api/admin/ops/services/web/restart')).toBe(0)
  fireEvent.change(dialog.querySelector('input[type="password"]') as HTMLInputElement,
    { target: { value: 'fixed-test-secret-ops1' } })
  fireEvent.submit(dialog.querySelector('form') as HTMLFormElement)
  expect(await screen.findByRole('status')).toHaveTextContent('已排程重新啟動 web')
  expect(calls(m, 'POST /api/admin/ops/services/web/restart')).toBe(1)
  expect(calls(m, 'POST /api/me/elevate')).toBe(1)
})

test('後端說需要重新驗證（403 elevation_required）：彈出驗證框，驗證後自動重試同一個操作', async () => {
  let first = true
  const m = stub(OPERATE, {
    'POST /api/admin/ops/services/web/restart': () => {
      if (first) { first = false; return json({ detail: '這項操作需要重新驗證密碼', code: 'elevation_required' }, 403) }
      return json(ACCEPTED, 202)
    },
    'POST /api/me/elevate': () => json(ELEVATED),
  }, ELEVATED)
  wrap(service('web', ['status', 'logs', 'restart']))
  fireEvent.click(await screen.findByRole('button', { name: '重新啟動' }))
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '重新啟動' }))
  const dialog = await screen.findByRole('dialog', { name: '重新驗證身分' })
  fireEvent.change(dialog.querySelector('input[type="password"]') as HTMLInputElement,
    { target: { value: 'fixed-test-secret-ops1' } })
  fireEvent.submit(dialog.querySelector('form') as HTMLFormElement)
  expect(await screen.findByRole('status')).toHaveTextContent('已排程重新啟動 web')
  expect(calls(m, 'POST /api/admin/ops/services/web/restart')).toBe(2)
  expect(calls(m, 'POST /api/me/elevate')).toBe(1)
})

test('重新驗證按了取消：操作不送出，也不當成錯誤', async () => {
  const m = stub(OPERATE, { 'POST /api/admin/ops/services/web/restart': () => json(ACCEPTED, 202) })
  wrap(service('web', ['status', 'logs', 'restart']))
  fireEvent.click(await screen.findByRole('button', { name: '重新啟動' }))
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '重新啟動' }))
  const dialog = await screen.findByRole('dialog', { name: '重新驗證身分' })
  fireEvent.click(within(dialog).getByRole('button', { name: '取消' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(calls(m, 'POST /api/admin/ops/services/web/restart')).toBe(0)
  // 取消之後按鈕可以再按
  await waitFor(() => expect(screen.getByRole('button', { name: '重新啟動' })).toBeEnabled())
})

test('同一組工作正在跑：409 already_running 顯示明確說明', async () => {
  stub(OPERATE, {
    'POST /api/admin/ops/services/sync/run': () => json({ detail: 'busy', code: 'already_running' }, 409),
  }, ELEVATED)
  wrap(service('sync', ['status', 'logs', 'run']))
  fireEvent.click(await screen.findByRole('button', { name: '立即執行' }))
  expect(screen.getByRole('alertdialog', { name: '立即執行 sync' })).toHaveTextContent('確定要立即執行「sync」一次？')
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '立即執行' }))
  const toast = await screen.findByRole('status')
  expect(toast).toHaveTextContent('同一組工作正在執行中')
  expect(toast).toHaveAttribute('data-tone', 'bad')
})

test.each([
  ['action_not_allowed', 403, '這個服務不允許這項操作。'],
  ['ops_agent_unavailable', 503, '維運代理目前無法使用，操作沒有送出。'],
  ['missing_scope', 403, '需要「ops.operate」權限。'],
  ['ops_timeout', 504, '代理逾時（後端的 detail 原樣顯示）'],
])('操作失敗（%s）：toast 顯示對應的說明', async (code, status, text) => {
  stub(OPERATE, {
    'POST /api/admin/ops/services/sync/run': () => json({ detail: '代理逾時（後端的 detail 原樣顯示）', code }, status),
  }, ELEVATED)
  wrap(service('sync', ['status', 'logs', 'run']))
  fireEvent.click(await screen.findByRole('button', { name: '立即執行' }))
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '立即執行' }))
  expect(await screen.findByRole('status')).toHaveTextContent(text)
})

test('立即執行成功', async () => {
  const m = stub(OPERATE, {
    'POST /api/admin/ops/services/backup/run': () => json({ ...ACCEPTED, name: 'backup', target: 'report-mark-backup.service', actions: ['status', 'logs', 'run'], group: 'backup', action: 'run', state: 'queued', execute_after_ms: 0 }, 202),
  }, ELEVATED)
  wrap(service('backup', ['status', 'logs', 'run']))
  fireEvent.click(await screen.findByRole('button', { name: '立即執行' }))
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '立即執行' }))
  expect(await screen.findByRole('status')).toHaveTextContent('已送出 backup，等同 timer 觸發一次')
  expect(calls(m, 'POST /api/admin/ops/services/backup/run')).toBe(1)
})
