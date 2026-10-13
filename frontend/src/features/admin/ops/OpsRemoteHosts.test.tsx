import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import OperationsLayout from './OperationsLayout'
import OpsRemoteHostsPage from './OpsRemoteHostsPage'
import { fmtAge, remoteTone } from './remoteHostLabels'

afterEach(() => vi.unstubAllGlobals())

const service = (s: Record<string, unknown>) => ({
  kind: 'systemd', tier: 'important', description: '', summary: 'idle', health: 'ok', health_reason: '排程待命',
  timer: null, timer_active: null, last_run_at: null, next_run_at: null, exit_status: null, ...s,
})

const SYNC_OK = {
  source: 'nas', inbox_push: true, timer_active: true, round_state: 'done', round_started_at: '2026-10-12T07:00:03Z',
  round_rc: 0, feed_round: '20261012_150003', feed_at: '2026-10-12T07:01:00Z', feed_result: 'ok', new_files: 4,
  push: 'ok', pending_replays: 0, last_success_at: '2026-10-12T07:20:00Z',
}

const HOST_OK = {
  name: 'office', state: 'ok', reasons: [], checked_at: '2026-10-12T08:00:00Z', published_at: '2026-10-12T07:55:00Z',
  age_seconds: 300, error: null, stale_after_minutes: 45, feed_stale_after_hours: 7, host: 'office-pc', sync: SYNC_OK,
  services: [
    service({ name: 'web', tier: 'critical', summary: 'running', health_reason: '執行中', description: 'Web' }),
    service({ name: 'sync', timer: 'report-mark-sync.timer', timer_active: true, description: 'NAS 增量同步匯入',
      last_run_at: '2026-10-12T07:20:00Z', next_run_at: '2026-10-12T10:00:00Z' }),
    service({ name: 'backup', summary: 'failed', health: 'down', health_reason: '最後一次執行失敗',
      description: '不可重建表備份' }),
  ],
  services_error: null,
}

function mount(reply: { status?: number; body: unknown }) {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/me') return new Response(JSON.stringify({ id: 'me', username: 'root', role: 'admin',
      scopes: ['admin', 'ops.read'] }))
    if (url === '/api/admin/ops/remote-hosts') return new Response(JSON.stringify(reply.body), { status: reply.status ?? 200 })
    return new Response(JSON.stringify({ detail: 'not found' }), { status: 404 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/operations/remote-hosts']}>
        <Routes>
          <Route path="/admin/operations" element={<OperationsLayout />}>
            <Route path="remote-hosts" element={<OpsRemoteHostsPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

test('正常：一行結論、來源段的事實與那台的服務清單（依層級分組）', async () => {
  const fetchMock = mount({ body: { items: [HOST_OK] } })
  expect(await screen.findByRole('heading', { name: /來源主機：office（office-pc）/ })).toHaveTextContent('正常')
  expect(fetchMock.mock.calls.map(([u]) => String(u))).toContain('/api/admin/ops/remote-hosts')
  expect(screen.getByRole('status')).toHaveTextContent('NAS 抓檔與推送到 R2 這一段正常')
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.getByText(/5 分鐘前/)).toBeInTheDocument()
  expect(screen.getByText(/超過 45 分鐘沒有回報視為失聯/)).toBeInTheDocument()

  const feed = within(screen.getByRole('region', { name: 'NAS 抓檔與上傳' }))
  expect(feed.getByText('NAS（rsync）')).toBeInTheDocument()
  expect(feed.getByText('啟用')).toBeInTheDocument()
  expect(feed.getByText('4')).toBeInTheDocument()
  expect(feed.getByText(/超過 7 小時沒有結果視為停擺/)).toBeInTheDocument()

  const services = within(screen.getByRole('region', { name: '那台主機的服務' }))
  expect(services.getByText(/共 3 項、1 項故障/)).toBeInTheDocument()
  expect(within(services.getByRole('region', { name: '核心服務' })).getByTestId('remote-service-web')).toBeInTheDocument()
  const backup = services.getByTestId('remote-service-backup')
  expect(backup).toHaveAttribute('data-health', 'down')
  expect(within(backup).getByText('最後一次執行失敗')).toBeInTheDocument()
  // 唯讀：那台的服務不能從這裡點進去操作
  expect(services.queryByRole('link')).not.toBeInTheDocument()
  expect(services.queryByRole('button')).not.toBeInTheDocument()
})

test('推送失敗：告警列出每一條成立的原因與影響', async () => {
  mount({ body: { items: [{ ...HOST_OK, state: 'push_failed', reasons: ['push_failed', 'round_aborted'],
    sync: { ...SYNC_OK, push: 'failed', pending_replays: 2, round_state: 'aborted' } }] } })
  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('來源異常：推送失敗、上一輪被中止')
  expect(alert).toHaveTextContent('要手動補傳')
  expect(alert).toHaveTextContent('要人工重放')
  expect(screen.getByRole('heading', { name: /來源主機：office/ })).toHaveTextContent('推送失敗')
  const feed = within(screen.getByRole('region', { name: 'NAS 抓檔與上傳' }))
  expect(feed.getByText('失敗')).toBeInTheDocument()
  expect(feed.getByText('2')).toBeInTheDocument()
  expect(feed.getByText(/被中止/)).toBeInTheDocument()
})

test('主機失聯：只有信標的年齡，沒有來源段與服務清單也不白屏', async () => {
  mount({ body: { items: [{ ...HOST_OK, state: 'stale', reasons: ['stale'], age_seconds: 7500, sync: null,
    services: null, host: null }] } })
  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('來源異常：主機失聯')
  expect(alert).toHaveTextContent('這段期間不會有新研報推上來')
  expect(screen.getByText(/2 小時 5 分前/)).toBeInTheDocument()
  expect(screen.queryByRole('region', { name: 'NAS 抓檔與上傳' })).not.toBeInTheDocument()
  expect(screen.getByText(/這次回報沒有帶服務清單/)).toBeInTheDocument()
})

test('判斷不出來（R2 讀不到）：說明不是故障，不用告警的樣式', async () => {
  mount({ body: { items: [{ ...HOST_OK, state: 'unknown', reasons: [], published_at: null, age_seconds: null,
    error: 'R2 讀取失敗：ObjectStorageError', sync: null, services: null, host: null }] } })
  expect(await screen.findByRole('status')).toHaveTextContent('判斷不出來源主機的狀態：R2 讀取失敗')
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByText(/這次回報沒有帶服務清單/)).not.toBeInTheDocument()
})

test('維運代理問不到時仍顯示來源段，並說明服務清單為什麼沒有', async () => {
  mount({ body: { items: [{ ...HOST_OK, services: null, services_error: '維運代理問不到：維運代理逾時（20 秒）',
    sync: { ...SYNC_OK, timer_active: null } }] } })
  expect(await screen.findByText(/這次回報沒有帶服務清單：維運代理問不到/)).toBeInTheDocument()
  expect(within(screen.getByRole('region', { name: 'NAS 抓檔與上傳' })).getByText(/不明（那台的維運代理沒有回報）/))
    .toBeInTheDocument()
})

test('沒設定要監看：空狀態說明怎麼啟用', async () => {
  mount({ body: { items: [] } })
  expect(await screen.findByText('這台沒有設定要監看的來源主機。')).toBeInTheDocument()
  expect(screen.getByText('HOST_BEACON_WATCH')).toBeInTheDocument()
})

test('頁首是「來源主機」，切頁交給外殼的次級面板（這裡沒有子導覽）', async () => {
  mount({ body: { items: [] } })
  expect(await screen.findByRole('heading', { level: 1, name: '來源主機' })).toBeInTheDocument()
  expect(screen.getByText('維運 / 健康')).toBeInTheDocument()
  expect(screen.queryByRole('navigation', { name: '維運子導覽' })).not.toBeInTheDocument()
})

test('fmtAge 與 remoteTone', () => {
  expect(fmtAge(20)).toBe('不到 1 分鐘前')
  expect(fmtAge(300)).toBe('5 分鐘前')
  expect(fmtAge(7500)).toBe('2 小時 5 分前')
  expect(fmtAge(100_000)).toBe('1 天 3 小時前')
  expect(fmtAge(null)).toBe('—')
  expect(remoteTone('ok')).toBe('ok')
  expect(remoteTone('unknown')).toBe('unknown')
  expect(remoteTone('disabled')).toBe('unknown')
  expect(remoteTone('stale')).toBe('alert')
})
