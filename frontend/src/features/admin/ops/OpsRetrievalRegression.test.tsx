import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import OperationsLayout from './OperationsLayout'
import OpsDataHealthPage from './OpsDataHealthPage'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const HEALTH = {
  generated_at: '2026-10-06T02:00:00Z',
  overall: 'ok',
  freshness: { status: 'ok', exit_code: 0, error: null, findings: [] },
  db_audit: {
    status: 'unknown', available: false, unavailable_reason: 'missing', finished_at: null, age_hours: null,
    stale: false, exit_code: null, error: null, skipped: [], findings: [],
  },
  r2_reconcile: {
    status: 'unknown', available: false, unavailable_reason: 'missing', finished_at: null, age_hours: null,
    stale: false, exit_code: null, mode: null, dry_run: null, limit: null, orphan_scan: null, stats: null,
    issues: [], issues_total: 0,
  },
}

const HASH = 'ab'.repeat(32)

const question = (id: string, over: Record<string, unknown> = {}) => ({
  id, question: `${id} 的題目`, comparable: true, degraded: false, report_recall: 1, raw_report_recall: 0.8,
  chunk_recall: 0.9, rbo: 0.95, baseline_reports: 5, eligible_reports: 5, current_reports: 5, hidden_reports: 0,
  removed_reports: 0, excluded_new_reports: 1, lost_total: 0, gained_total: 0, lost: [], gained: [], ...over,
})

const COMPARISON = {
  finished_at: '2026-10-06T07:43:00Z', duration_s: 182.4, dense_scan: 400, params_changed: false,
  baseline: {
    captured_at: '2026-09-30T08:00:00Z', corpus_cutoff: '2026-09-30T06:20:00Z', corpus_reports: 15060,
    simulated_as_of: false, k: 10, dense_scan: 400, dataset_sha256: '0'.repeat(64),
  },
  thresholds: { min_mean_recall: 0.8, min_question_recall: 0.5, max_degraded_questions: 2 },
  summary: {
    verdict: 'degraded', questions: 18, comparable: 18, mean_report_recall: 0.62, mean_raw_report_recall: 0.41,
    mean_chunk_recall: 0.5, mean_rbo: 0.55, degraded_questions: 3, hidden_reports: 2, removed_reports: 1,
    excluded_new_reports: 27, lex_truncated_questions: 1,
  },
  questions: [
    question('q001', {
      degraded: true, lex_truncated: true, report_recall: 0.2, lost_total: 4, gained_total: 1,
      lost: [{ file_hash: HASH, label: '某券商 台積電展望' }], gained: [{ file_hash: 'cd'.repeat(32), label: null }],
    }),
    question('q002'),
    question('q003', { comparable: false, report_recall: null }),
  ],
}

function mount(rr: unknown, status = 200) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url === '/api/me') return new Response(JSON.stringify({ id: 'me', username: 'root', role: 'admin', scopes: ['admin', 'ops.read'] }))
    if (url.startsWith('/api/admin/retrieval-regression')) return new Response(JSON.stringify(rr), { status })
    return new Response(JSON.stringify(HEALTH))
  }))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/operations/data-health']}>
        <Routes>
          <Route path="/admin/operations" element={<OperationsLayout />}>
            <Route path="data-health" element={<OpsDataHealthPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

test('檢索回歸：劣化時顯示摘要、逐題狀態、流失研報連到閱讀頁', async () => {
  mount({
    status: 'fail', available: true, unavailable_reason: null, finished_at: '2026-10-06T07:43:00Z', age_hours: 1,
    stale: false, exit_code: 1, outcome: 'degraded', reason: null, message: '檢索劣化：平均研報召回 0.62',
    comparison: COMPARISON,
  })
  expect(await screen.findByRole('heading', { name: '檢索回歸' })).toBeInTheDocument()
  const summary = await screen.findByLabelText('檢索回歸摘要')
  expect(within(summary).getByText('劣化')).toBeInTheDocument()
  expect(within(summary).getByText(/62%（門檻 80%）/)).toBeInTheDocument()
  expect(within(summary).getByText(/排除新研報 27、基準研報被隱藏 2、已下架 1/)).toBeInTheDocument()
  expect(screen.getByText('檢索劣化：平均研報召回 0.62')).toBeInTheDocument()
  const table = screen.getByRole('table', { name: '檢索回歸逐題' })
  const rows = within(table).getAllByRole('row').slice(1)
  expect(rows).toHaveLength(3)
  expect(within(rows[0]).getByText('劣化')).toBeInTheDocument()
  expect(within(rows[0]).getByText('字面路截斷')).toBeInTheDocument()
  expect(within(rows[1]).queryByText('字面路截斷')).not.toBeInTheDocument()
  expect(within(summary).getByText(/^1 題（候選超過上限/)).toBeInTheDocument()
  expect(within(rows[1]).getByText('正常')).toBeInTheDocument()
  expect(within(rows[2]).getByText('不可比')).toBeInTheDocument()
  expect(within(rows[0]).getByRole('link', { name: '某券商 台積電展望' })).toHaveAttribute('href', `/report/${HASH}`)
  expect(within(rows[0]).getByRole('link', { name: /^cdcdcdcdcdcd/ })).toBeInTheDocument()
})

test('檢索回歸：略過的那次說明原因，仍顯示上一次比對；dense_scan 改過要提醒', async () => {
  mount({
    status: 'warn', available: true, unavailable_reason: null, finished_at: '2026-10-07T07:41:00Z', age_hours: 0.5,
    stale: true, exit_code: 2, outcome: 'skipped', reason: 'low_memory', message: '可用記憶體 2.1 GiB < 4 GiB',
    comparison: { ...COMPARISON, params_changed: true, dense_scan: 200, summary: { ...COMPARISON.summary, verdict: 'ok' } },
  })
  expect(await screen.findByText(/最近一次略過：可用記憶體不足（不告警）/)).toBeInTheDocument()
  expect(screen.getByText(/超過 48 小時沒有真的比對過/)).toBeInTheDocument()
  expect(screen.getByText(/dense_scan 現在是 200，基準擷取時是 400/)).toBeInTheDocument()
  expect(within(screen.getByLabelText('檢索回歸摘要')).getByText('沒有劣化')).toBeInTheDocument()
})

test('檢索回歸：還沒有結果檔時說明要先擷取基準；端點失敗不影響資料健康其他段', async () => {
  mount({
    status: 'unknown', available: false, unavailable_reason: 'missing', finished_at: null, age_hours: null,
    stale: false, exit_code: null, outcome: null, reason: null, message: null, comparison: null,
  })
  expect(await screen.findByText(/還沒有結果檔。每日 07:40/)).toBeInTheDocument()
  expect(screen.getByText('scripts/retrieval_regression.py capture')).toBeInTheDocument()
})

test('檢索回歸：API 失敗只影響這張卡', async () => {
  mount({ detail: '需要權限：ops.read', code: 'missing_scope' }, 403)
  expect(await screen.findByText('檢索回歸載入失敗：需要權限：ops.read')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '資料健康' })).toBeInTheDocument()
})
