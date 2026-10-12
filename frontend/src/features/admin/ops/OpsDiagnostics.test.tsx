import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import * as clipboard from '../../../lib/clipboard'
import OperationsLayout from './OperationsLayout'
import OpsDiagnosticsPage from './OpsDiagnosticsPage'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const SHA_START = '8aa38ce19e5dac15192dedf26d8db8cddaff894d'
const SHA_DISK = 'aacec4500000000000000000000000000000beef'

const DIAG = {
  generated_at: '2026-10-06T12:00:00Z',
  cache_ttl_s: 30,
  versions: {
    git: {
      available: true, reason: null, commit_at_start: SHA_START, branch_at_start: 'main',
      commit_on_disk: SHA_DISK, branch_on_disk: 'main', restart_pending: true,
    },
    frontend: { available: true, built_at: '2026-10-06T11:00:00Z', entry_assets: ['index-AbC123.js', 'index-Zz9.css'] },
    python: '3.13.11', platform: 'Linux 6.1 x86_64',
    packages: { fastapi: '0.141.1', torch: null },
    error: null,
  },
  schema_info: {
    status: 'behind', db_revisions: ['0006'], code_heads: ['0007'], pending: ['0007'], error: null,
    daily_check: {
      available: true, unavailable_reason: null, checked_at: '2026-10-06T05:20:03+08:00', age_hours: 6.5, stale: false,
      mode: 'full', exit_code: 1, alert: true, problems: ['version_behind'], message: 'DB 落後', version_status: 'behind',
      drift_status: 'ok', drift_count: 0, error: null,
    },
  },
  runtime: {
    pid: 1234, hostname: 'prod-host', started_at: '2026-10-06T10:00:00Z', uptime_s: 7200, rss_bytes: 2147483648,
    peak_rss_bytes: 3221225472, threads: 40, timezone: { tz_env: null, name: 'CST', utc_offset: '+0800' },
    python_executable: '/srv/report-mark/.venv/bin/python3', error: null,
  },
  config: {
    db_target: 'localhost:5436/research', object_storage_mode: 'r2', llm_provider: 'deepseek',
    models: { ask_answer: 'deepseek-flash' }, extractor: 'pypdf', log_level: 'INFO', ops_agent_environment: 'production',
    ops_agent_socket: '/run/report-mark-ops/agent.sock',
    flags: { ask_enable_web: false, ask_rerank_enabled: true, qa_agentic_enabled: true, ask_faithfulness_enabled: true,
      trusted_data_enabled: true, skip_warmup: false, dev_no_auth: false },
    secrets_present: { deepseek_api_key: true, session_secret: true, edge_secret: false, r2_credentials: true, alert_webhook: true },
    limits: { db_pool_size: 5, db_max_overflow: 15, db_pool_timeout_s: 10, db_statement_timeout_ms: 60000,
      db_idle_tx_timeout_ms: 0, embed_max_concurrency: 1, ask_faithfulness_sample_rate: 1, ask_faithfulness_max_inflight: 2 },
    error: null,
  },
  models: { embed_model: 'BAAI/bge-m3', embed_loaded: true, rerank_loaded: false, rerank_load_failed: false, warmup: 'done', warmup_error: null, error: null },
  db_pool: { pool_class: 'AsyncAdaptedQueuePool', size: 5, max_overflow: 15, checked_out: 2, checked_in: 3, open_connections: 5, error: null },
  gates: [{ name: 'ask', capacity: 3, in_use: 1, waiting: 0, max_queue: 20 }],
  gates_error: null,
  checks: {
    db: { ok: true, latency_ms: 1.2, server_version: '16.4', error: null },
    storage: { state: 'ok', consecutive_failures: 0, last_probe_age_s: 120, last_probe_ok: true, error: null },
    llm: { state: 'low', key_configured: true, ask_uses_http: true, consecutive_failures: 0, last_check_age_s: 30, quota_latched: false, error: null },
    ops_agent: { ok: false, latency_ms: null, services: null, error: 'OpsAgentUnavailable' },
  },
}

function mount(body: unknown = DIAG) {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/me') return new Response(JSON.stringify({ id: 'me', username: 'root', role: 'admin', scopes: ['admin', 'ops.read'] }))
    if (url === '/api/admin/diagnostics') return new Response(JSON.stringify(body))
    return new Response('{}', { status: 404 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/operations/diagnostics']}>
        <Routes>
          <Route path="/admin/operations" element={<OperationsLayout />}>
            <Route path="diagnostics" element={<OpsDiagnosticsPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

test('診斷頁：分區顯示摘要、版本、schema、連通性', async () => {
  mount()
  expect(await screen.findByRole('heading', { name: '診斷' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '診斷' })).toHaveAttribute('href', '/admin/operations/diagnostics')

  const summary = screen.getByRole('list', { name: '診斷摘要' })
  expect(within(summary).getByText('DB 落後')).toBeInTheDocument()
  expect(within(summary).getByText('待重啟')).toBeInTheDocument()
  expect(within(summary).getByText('餘額偏低')).toBeInTheDocument()
  expect(within(summary).getByText('OpsAgentUnavailable')).toBeInTheDocument()

  expect(screen.getByText(/程式已更新但 web 還沒重啟/)).toBeInTheDocument()
  expect(screen.getByText(SHA_START)).toBeInTheDocument()
  expect(screen.getByText('index-AbC123.js、index-Zz9.css')).toBeInTheDocument()
  expect(screen.getByText('未安裝')).toBeInTheDocument()

  const schema = screen.getByRole('region', { name: 'Schema' })
  expect(within(schema).getByText('0006')).toBeInTheDocument()
  expect(within(schema).getByText('version_behind')).toBeInTheDocument()

  expect(screen.getByLabelText('祕密')).toHaveTextContent('edge_secret未設')
})

test('複製診斷包：整份 JSON 經 copyText', async () => {
  const copy = vi.spyOn(clipboard, 'copyText').mockResolvedValue()
  mount()
  fireEvent.click(await screen.findByRole('button', { name: '複製診斷包' }))
  await waitFor(() => expect(copy).toHaveBeenCalledTimes(1))
  expect(JSON.parse(copy.mock.calls[0][0])).toEqual(DIAG)
  expect(await screen.findByText('已複製（JSON，不含祕密）')).toBeInTheDocument()
})

test('複製失敗：顯示可手動複製的文字框', async () => {
  vi.spyOn(clipboard, 'copyText').mockRejectedValue(new Error('copy failed'))
  mount()
  fireEvent.click(await screen.findByRole('button', { name: '複製診斷包' }))
  expect(await screen.findByText('無法自動複製，請從下方手動複製')).toBeInTheDocument()
  const box = screen.getByLabelText('診斷包 JSON') as HTMLTextAreaElement
  expect(JSON.parse(box.value).generated_at).toBe(DIAG.generated_at)
})

test('某段讀取失敗：只在該段標示錯誤', async () => {
  mount({
    ...DIAG,
    runtime: { error: 'PermissionError' },
    checks: { ...DIAG.checks, db: { ok: false, latency_ms: null, server_version: null, error: 'TimeoutError' } },
    schema_info: { ...DIAG.schema_info, status: 'error', db_revisions: [], pending: [], error: 'TimeoutError' },
  })
  expect(await screen.findByText('這一段讀取失敗：PermissionError')).toBeInTheDocument()
  const summary = screen.getByRole('list', { name: '診斷摘要' })
  expect(within(summary).getAllByText('連不上')).toHaveLength(2) // DB 與維運代理
  expect(within(summary).getByText('查不到')).toBeInTheDocument()
})
