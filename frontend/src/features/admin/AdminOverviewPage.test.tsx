import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import AdminOverviewPage from './AdminOverviewPage'
import { progressFixture } from './ops/pipeline/pipelineTestKit'

afterEach(() => vi.unstubAllGlobals())

const svc = (name: string, tier: string, summary: string) => ({
  name, kind: 'systemd', tier, target: `report-mark-${name}.service`, timer: null, actions: ['status'],
  description: `${name} 說明`, summary, error: null, systemd: null, container: null, timer_state: null,
})
const SERVICES = {
  environment: 'production', host: 'office-host', checked_at: '2026-10-06T02:00:00Z',
  items: [
    svc('web', 'critical', 'running'), svc('postgres', 'critical', 'running'), svc('nginx', 'critical', 'idle'),
    svc('sync', 'important', 'idle'), svc('backup', 'important', 'failed'), svc('freshness', 'supporting', 'idle'),
  ],
}
const section = { status: 'ok', available: true, stale: false, skipped: [], findings: [] }
const DATA_HEALTH = {
  generated_at: 'x', overall: 'ok', freshness: { status: 'ok', exit_code: 0, findings: [] },
  db_audit: section, r2_reconcile: { ...section, issues: [], issues_total: 0 },
}
const ALERTS = {
  state: 'ok', triggered: [], window_minutes: 15, login_failures: 0, login_failure_threshold: 20, accounts_over: [],
  max_account_failures: 0, account_failure_threshold: 5, elevate_failures: 0, elevate_failure_threshold: 5, audit_chain: 'ok',
}
const page = (total: number, extra: Record<string, unknown> = {}) =>
  ({ total, limit: 1, offset: 0, has_more: false, next_offset: null, items: [], ...extra })

type Over = (url: string) => { status?: number; body: unknown } | undefined

function mount(scopes: string[], over?: Over) {
  const fetchMock = vi.fn(async (url: string) => {
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
    const o = over?.(url)
    if (o) return json(o.body, o.status)
    if (url === '/api/me') return json({ id: 'me', username: 'root', role: 'admin', scopes })
    if (url === '/api/admin/ops/services') return json(SERVICES)
    if (url === '/api/progress') return json(progressFixture())
    if (url.startsWith('/api/admin/incidents')) return json({ since: 'a', until: 'b', ...page(0) })
    if (url === '/api/admin/data-health') return json(DATA_HEALTH)
    if (url === '/api/admin/security/alerts') return json(ALERTS)
    if (url.startsWith('/api/review/queue')) {
      const kind = new URLSearchParams(url.split('?')[1]).get('kind')!
      return json(page({ faithfulness: 8, feedback: 3, extraction: 5 }[kind] ?? 0, { kind }))
    }
    if (url.startsWith('/api/admin/uploads')) return json(page(3, { scanner: { pending: 0, scanning: 0 } }))
    return json({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin']}><AdminOverviewPage /></MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

const ALL = ['admin', 'accounts.manage', 'audit.read', 'review.manage', 'ops.read', 'reports.manage', 'analytics.read']
const tile = (name: RegExp) => within(screen.getByRole('region', { name: '狀態' })).getByRole('link', { name })

test('狀態磚：服務有失敗時亮紅並列出是哪幾個，其餘正常；摘要一句講完', async () => {
  mount(ALL)
  expect(await screen.findByText('4／6')).toBeInTheDocument()
  expect(tile(/服務/)).toHaveAttribute('href', '/admin/operations/services')
  expect(tile(/服務/)).toHaveTextContent('2 個需要處理：nginx、backup')
  expect(await screen.findByText('近 24 小時沒有排程失敗')).toBeInTheDocument()
  expect(tile(/管線/)).toHaveAttribute('href', '/admin/operations/pipeline')
  expect(await screen.findByText('沒有進行中的事件')).toBeInTheDocument()
  expect(await screen.findByText('沒有觸發中的告警')).toBeInTheDocument()
  expect(tile(/安全/)).toHaveAttribute('href', '/admin/security')
  expect(await screen.findByText('新鮮度、稽核、R2 對帳都正常')).toBeInTheDocument()
  expect(await screen.findByText('1 項異常、0 項注意・16 筆待複核・3 份待審草稿')).toBeInTheDocument()
})

test('異常與注意：列出亮燈的頁面與原因，按鈕連到該頁', async () => {
  mount(ALL)
  const card = within(await screen.findByRole('region', { name: '異常與注意' }))
  expect(await card.findByText('2 個服務需要處理')).toBeInTheDocument()
  expect(card.getByText('nginx、backup')).toBeInTheDocument()
  expect(card.getByRole('link', { name: '看服務' })).toHaveAttribute('href', '/admin/operations/services')
})

test('待處理：待複核與待審草稿只有數字，不算進異常', async () => {
  mount(ALL)
  const queues = within(await screen.findByRole('region', { name: '待處理' }))
  expect(await queues.findByText('16')).toBeInTheDocument()
  expect(queues.getByRole('link', { name: /待複核/ })).toHaveAttribute('href', '/admin/reviews')
  expect(queues.getByText('忠實度低分 8・倒讚 3・抽取品質 5')).toBeInTheDocument()
  expect(queues.getByRole('link', { name: /待審草稿/ })).toHaveAttribute('href', '/admin/uploads?tab=draft')
  const alerts = within(screen.getByRole('region', { name: '異常與注意' }))
  expect(alerts.queryByText(/待複核/)).not.toBeInTheDocument()
})

test('服務：各層正常／總數（排程服務閒置算正常），需要處理的服務連到詳情（核心停著也算）', async () => {
  mount(ALL)
  expect(within(await screen.findByLabelText('核心服務')).getByText('2／3')).toBeInTheDocument()
  expect(within(screen.getByLabelText('重要服務')).getByText('1／2')).toBeInTheDocument()
  expect(within(screen.getByLabelText('輔助服務')).getByText('1／1')).toBeInTheDocument()
  const svcCard = within(screen.getByRole('region', { name: '服務' }))
  expect(svcCard.getByRole('link', { name: 'nginx' })).toHaveAttribute('href', '/admin/operations/services/nginx')
  expect(svcCard.getByRole('link', { name: 'backup' })).toBeInTheDocument()
  expect(svcCard.queryByRole('link', { name: 'web' })).not.toBeInTheDocument()
  expect(svcCard.queryByRole('link', { name: 'sync' })).not.toBeInTheDocument()
})

test('全部正常：沒有異常', async () => {
  mount(ALL, url => url === '/api/admin/ops/services'
    ? { body: { ...SERVICES, items: [svc('web', 'critical', 'running')] } } : undefined)
  expect(await screen.findByText('沒有失敗的服務')).toBeInTheDocument()
  expect(tile(/服務/)).toHaveTextContent('1／1')
  expect(screen.getByText('沒有異常')).toBeInTheDocument()
})

test('維運代理不可用：服務磚寫「讀不到」，不講正常；其他磚照常', async () => {
  mount(ALL, url => url === '/api/admin/ops/services'
    ? { status: 503, body: { detail: '維運代理不可用', code: 'ops_agent_unavailable' } } : undefined)
  expect(await screen.findByText('讀不到')).toBeInTheDocument()
  expect(tile(/服務/)).toHaveTextContent('維運代理連不上')
  expect(await screen.findByText('近 24 小時沒有排程失敗')).toBeInTheDocument()
  expect(screen.queryByRole('region', { name: '服務' })).not.toBeInTheDocument()
})

test('沒有對應 scope 的區塊不畫，也不送那些請求', async () => {
  const fetchMock = mount(['admin', 'review.manage'])
  expect(await screen.findByText('0 項異常、0 項注意・16 筆待複核')).toBeInTheDocument()
  expect(screen.queryByRole('region', { name: '狀態' })).not.toBeInTheDocument()
  expect(screen.queryByText('待審草稿')).not.toBeInTheDocument()
  const urls = fetchMock.mock.calls.map(c => String(c[0]))
  expect(urls.some(u => u.startsWith('/api/admin/') || u === '/api/progress')).toBe(false)
})

test('一般使用者：無權限頁，不送任何管理請求', async () => {
  const fetchMock = mount([], url => url === '/api/me'
    ? { body: { id: 'u', username: 'someone', role: 'user' } } : undefined)
  expect(await screen.findByRole('heading', { name: '需要管理員權限' })).toBeInTheDocument()
  expect(fetchMock.mock.calls.map(c => c[0])).toEqual(['/api/me'])
})
