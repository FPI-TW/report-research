import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { OpsServiceDetail } from '../../../lib/generated/adminApi'
import { OpsServiceActions } from './OpsServiceActions'

afterEach(() => vi.unstubAllGlobals())

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

/** scopes：/api/me 回的實際 scope；handlers：依 URL＋method 回應，其餘 404。 */
function stub(scopes: string[], handlers: Record<string, (init?: RequestInit) => Response>) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/me') {
      return json({ id: 'u1', username: 'ops', role: 'admin', is_super: false, scopes, totp_enabled: false })
    }
    const h = handlers[`${init?.method ?? 'GET'} ${url}`]
    return h ? h(init) : json({ detail: 'not found', code: 'not_found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function wrap(svc: OpsServiceDetail) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><OpsServiceActions service={svc} /></QueryClientProvider>)
}

const calls = (m: ReturnType<typeof stub>, key: string) =>
  m.mock.calls.filter(([url, init]) => `${(init as RequestInit | undefined)?.method ?? 'GET'} ${url}` === key).length

test('沒有 ops.operate 的管理員看不到任何操作按鈕', async () => {
  stub(['admin', 'ops.read'], {})
  wrap(service('web', ['status', 'logs', 'restart']))
  await waitFor(() => expect(screen.queryByRole('button', { name: '重新啟動' })).not.toBeInTheDocument())
  expect(screen.queryByLabelText('服務操作')).not.toBeInTheDocument()
})

test('catalog 沒開放寫入的服務（唯讀）不顯示按鈕', async () => {
  stub(['admin', 'ops.read', 'ops.operate'], {})
  wrap(service('postgres', ['status', 'logs']))
  await new Promise((r) => setTimeout(r, 20))
  expect(screen.queryByRole('button', { name: /重新啟動|立即執行/ })).not.toBeInTheDocument()
})

test('重新啟動要先確認；取消不送出', async () => {
  const m = stub(['admin', 'ops.read', 'ops.operate'], { 'POST /api/admin/ops/services/web/restart': () => json(ACCEPTED, 202) })
  wrap(service('web', ['status', 'logs', 'restart']))
  fireEvent.click(await screen.findByRole('button', { name: '重新啟動' }))
  expect(screen.getByText(/確定要重新啟動「web」/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '取消' }))
  expect(calls(m, 'POST /api/admin/ops/services/web/restart')).toBe(0)
})

test('確認後送出重新啟動，顯示已送出', async () => {
  const m = stub(['admin', 'ops.read', 'ops.operate'], { 'POST /api/admin/ops/services/web/restart': () => json(ACCEPTED, 202) })
  wrap(service('web', ['status', 'logs', 'restart']))
  fireEvent.click(await screen.findByRole('button', { name: '重新啟動' }))
  fireEvent.click(screen.getByRole('button', { name: '確定重新啟動' }))
  expect(await screen.findByRole('status')).toHaveTextContent('已送出重新啟動')
  expect(calls(m, 'POST /api/admin/ops/services/web/restart')).toBe(1)
})

test('需要重新驗證：彈出驗證框，驗證後自動重試同一個操作', async () => {
  let first = true
  const m = stub(['admin', 'ops.read', 'ops.operate'], {
    'POST /api/admin/ops/services/web/restart': () => {
      if (first) { first = false; return json({ detail: '這項操作需要重新驗證密碼', code: 'elevation_required' }, 403) }
      return json(ACCEPTED, 202)
    },
    'POST /api/admin/elevate': () => json({ elevated_until: '2026-10-06T02:10:00Z' }),
  })
  wrap(service('web', ['status', 'logs', 'restart']))
  fireEvent.click(await screen.findByRole('button', { name: '重新啟動' }))
  fireEvent.click(screen.getByRole('button', { name: '確定重新啟動' }))
  const dialog = await screen.findByRole('dialog')
  fireEvent.change(dialog.querySelector('input[type="password"]') as HTMLInputElement,
    { target: { value: 'fixed-test-secret-ops1' } })
  fireEvent.submit(dialog.querySelector('form') as HTMLFormElement)
  expect(await screen.findByRole('status')).toHaveTextContent('已送出重新啟動')
  expect(calls(m, 'POST /api/admin/ops/services/web/restart')).toBe(2)
  expect(calls(m, 'POST /api/admin/elevate')).toBe(1)
})

test('同一組工作正在跑：409 already_running 顯示明確說明', async () => {
  stub(['admin', 'ops.read', 'ops.operate'], {
    'POST /api/admin/ops/services/sync/run': () => json({ detail: 'busy', code: 'already_running' }, 409),
  })
  wrap(service('sync', ['status', 'logs', 'run']))
  fireEvent.click(await screen.findByRole('button', { name: '立即執行' }))
  fireEvent.click(screen.getByRole('button', { name: '確定立即執行' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('同一組工作正在執行中')
})

test('立即執行成功', async () => {
  const m = stub(['admin', 'ops.read', 'ops.operate'], {
    'POST /api/admin/ops/services/backup/run': () => json({ ...ACCEPTED, name: 'backup', target: 'report-mark-backup.service', actions: ['status', 'logs', 'run'], group: 'backup', action: 'run', state: 'queued' }, 202),
  })
  wrap(service('backup', ['status', 'logs', 'run']))
  fireEvent.click(await screen.findByRole('button', { name: '立即執行' }))
  fireEvent.click(screen.getByRole('button', { name: '確定立即執行' }))
  expect(await screen.findByRole('status')).toHaveTextContent('已送出立即執行')
  expect(calls(m, 'POST /api/admin/ops/services/backup/run')).toBe(1)
})
