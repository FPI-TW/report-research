import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import OperationsLayout from './OperationsLayout'
import OpsDependenciesPage from './OpsDependenciesPage'

afterEach(() => vi.unstubAllGlobals())

type N = {
  name: string; kind?: string; tier?: string; health?: string; health_reason?: string; probe?: string | null
  depends_on?: string[]; dependents?: string[]; layer?: number; affected?: boolean; impacted_by?: string[]
  description?: string; observed_at?: string | null
}
const node = (n: N) => ({
  kind: 'systemd', tier: 'critical', target: null, description: '', summary: 'running', health: 'ok',
  health_reason: '執行中', probe: null, observed_at: null, depends_on: [], dependents: [], layer: 0, affected: false,
  impacted_by: [], ...n,
})

// postgres 壞了：web → nginx → 對外入口一路受影響；R2 是外部依賴（探針正常）、NAS 未監控、metrics 沒有依賴。
const DOWN_GRAPH = {
  environment: 'production', host: 'office-host', checked_at: '2026-10-06T02:00:00Z',
  nodes: [
    node({ name: 'postgres', kind: 'container', health: 'down', health_reason: '服務失敗', dependents: ['sync', 'web'],
      description: 'PostgreSQL＋pgvector' }),
    node({ name: 'r2', kind: 'external', health: 'ok', health_reason: '探針 health 退出碼 0', probe: 'health',
      dependents: ['web'], observed_at: '2026-10-06T01:58:00Z' }),
    node({ name: 'nas', kind: 'external', tier: 'important', health: 'unknown', health_reason: '未監控（沒有探針）',
      dependents: ['sync'] }),
    node({ name: 'metrics', tier: 'supporting' }),
    node({ name: 'web', depends_on: ['postgres', 'r2'], dependents: ['nginx'], layer: 1, affected: true,
      impacted_by: ['postgres'] }),
    node({ name: 'sync', tier: 'important', health_reason: '排程待命', depends_on: ['nas', 'postgres'], layer: 1,
      affected: true, impacted_by: ['postgres'] }),
    node({ name: 'nginx', kind: 'container', depends_on: ['web'], dependents: ['public-edge'], layer: 2,
      affected: true, impacted_by: ['postgres'] }),
    node({ name: 'public-edge', kind: 'external', health: 'unknown', health_reason: '探針 edge-health 退出碼 3：可能被其他狀況蓋住，判斷不出這一項',
      probe: 'edge-health', depends_on: ['nginx'], layer: 3, affected: true, impacted_by: ['postgres'] }),
  ],
  edges: [
    { dependent: 'web', dependency: 'postgres', broken: true },
    { dependent: 'web', dependency: 'r2', broken: false },
    { dependent: 'sync', dependency: 'nas', broken: false },
    { dependent: 'sync', dependency: 'postgres', broken: true },
    { dependent: 'nginx', dependency: 'web', broken: true },
    { dependent: 'public-edge', dependency: 'nginx', broken: true },
  ],
  down: ['postgres'],
  root_causes: ['postgres'],
  affected: ['nginx', 'public-edge', 'sync', 'web'],
}

function mount(reply: { status?: number; body: unknown }) {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/me') return new Response(JSON.stringify({ id: 'me', username: 'root', role: 'admin',
      scopes: ['admin', 'ops.read'] }))
    if (url === '/api/admin/ops/dependencies') return new Response(JSON.stringify(reply.body), { status: reply.status ?? 200 })
    return new Response(JSON.stringify({ detail: 'not found' }), { status: 404 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/operations/dependencies']}>
        <Routes>
          <Route path="/admin/operations" element={<OperationsLayout />}>
            <Route path="dependencies" element={<OpsDependenciesPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

test('依賴圖：根因與受影響的下游一眼看出，故障節點與斷掉的邊在圖上標出', async () => {
  const fetchMock = mount({ body: DOWN_GRAPH })
  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('根因：postgres（PostgreSQL＋pgvector）')
  expect(alert).toHaveTextContent('受影響的下游（4）：nginx、public-edge、sync、web')
  expect(fetchMock.mock.calls.map(([u]) => String(u))).toContain('/api/admin/ops/dependencies')

  const graph = screen.getByRole('img', { name: /1 個節點故障、4 個受影響/ })
  const pg = within(graph).getByTestId('node-postgres')
  expect(pg).toHaveAttribute('data-health', 'down')
  expect(within(graph).getByTestId('node-web')).toHaveAttribute('data-affected', 'true')
  expect(within(graph).getByTestId('node-r2')).toHaveAttribute('data-affected', 'false')
  expect(within(graph).getByTestId('edge-postgres-web')).toHaveAttribute('data-broken', 'true')
  expect(within(graph).getByTestId('edge-r2-web')).toHaveAttribute('data-broken', 'false')
  // 沒有依賴關係的節點不進圖，另外列出
  expect(within(graph).queryByTestId('node-metrics')).not.toBeInTheDocument()
  expect(screen.getByText('沒有依賴關係的節點：')).toHaveTextContent('metrics')
})

test('清單（手機上取代圖）：有問題的排前面，服務可連到詳情，外部依賴沒探針標「未監控」', async () => {
  mount({ body: DOWN_GRAPH })
  const list = within(await screen.findByRole('list', { name: '依賴清單' }))
  const items = list.getAllByRole('listitem').map(li => li.getAttribute('data-testid'))
  expect(items.slice(0, 5)).toEqual(['item-postgres', 'item-web', 'item-sync', 'item-nginx', 'item-public-edge'])
  const web = within(list.getByTestId('item-web'))
  expect(web.getByRole('link', { name: 'web' })).toHaveAttribute('href', '/admin/operations/services/web')
  expect(web.getByText('受影響')).toBeInTheDocument()
  expect(web.getByText(/上游故障：postgres/)).toBeInTheDocument()
  expect(web.getByText(/依賴 postgres、r2/)).toBeInTheDocument()
  const nas = within(list.getByTestId('item-nas'))
  expect(nas.getByText('未監控')).toBeInTheDocument()
  expect(nas.queryByRole('link')).not.toBeInTheDocument()  // 外部依賴沒有服務詳情頁
  expect(within(list.getByTestId('item-public-edge')).getByText('不明')).toBeInTheDocument()
})

test('全部正常：一行帶過，並說明不明／未監控的節點不列入傳播', async () => {
  mount({ body: { ...DOWN_GRAPH, down: [], root_causes: [], affected: [],
    nodes: DOWN_GRAPH.nodes.map(n => ({ ...n, health: n.health === 'down' ? 'ok' : n.health, affected: false,
      impacted_by: [] })),
    edges: DOWN_GRAPH.edges.map(e => ({ ...e, broken: false })) } })
  expect(await screen.findByRole('status')).toHaveTextContent('沒有故障的節點（2 個狀態不明或未監控，不列入傳播）')
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.getByRole('img', { name: /沒有故障/ })).toBeInTheDocument()
})

test('代理不可用：整頁換成降級說明，不白屏', async () => {
  mount({ status: 503, body: { detail: '維運代理不可用：維運代理未啟動', code: 'ops_agent_unavailable' } })
  expect(await screen.findByRole('heading', { name: '維運代理目前無法使用' })).toBeInTheDocument()
})
