import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import type { Progress } from '../../../../lib/progressSchema'
import OperationsLayout from '../OperationsLayout'
import OpsPipelinePage from './OpsPipelinePage'
import { progressFixture } from './pipelineTestKit'

afterEach(() => vi.unstubAllGlobals())

type Body = Progress | { status: number }

/** replies 依序回給每一次 /api/progress；用完後重複最後一個。 */
function mount(replies: Body[], opts: { scopes?: string[] } = {}) {
  const scopes = opts.scopes ?? ['admin', 'ops.read']
  let n = 0
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/me') {
      return new Response(JSON.stringify({ id: 'me', username: 'root', role: 'admin', scopes }), { status: 200 })
    }
    if (url === '/api/progress') {
      const r = replies[Math.min(n++, replies.length - 1)]
      if ('status' in r && typeof r.status === 'number' && !('ts' in r)) {
        return new Response(JSON.stringify({ detail: '伺服器錯誤' }), { status: r.status })
      }
      return new Response(JSON.stringify(r), { status: 200 })
    }
    return new Response(JSON.stringify({ detail: 'not found' }), { status: 404 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/operations/pipeline']}>
        <Routes>
          <Route path="/admin/operations" element={<OperationsLayout />}>
            <Route path="pipeline" element={<OpsPipelinePage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

const progressCalls = (f: ReturnType<typeof mount>) => f.mock.calls.filter(([u]) => u === '/api/progress').length
const card = (name: string) => within(screen.getByRole('region', { name }))

test('維運子導覽有「管線」且是目前分頁；一切正常 → 管線正常、沒有待處理清單', async () => {
  mount([progressFixture()])
  expect(await screen.findByText('管線正常')).toBeInTheDocument()
  const tabs = within(screen.getByRole('navigation', { name: '維運子導覽' }))
  expect(tabs.getByRole('link', { name: '管線' })).toHaveAttribute('aria-current', 'page')
  const summary = card('管線狀態')
  expect(summary.queryByRole('list', { name: '需要處理的項目' })).not.toBeInTheDocument()
  expect(summary.getByText('14:32:05')).toBeInTheDocument()
  expect(summary.getByText(/每 15 秒/)).toBeInTheDocument()
})

test('待人看：低於門檻與抽取需複核只列數字並連到待複核，不影響結論', async () => {
  mount([progressFixture()])
  const summary = within(await screen.findByRole('region', { name: '管線狀態' }))
  expect(summary.getByText('管線正常')).toBeInTheDocument()
  expect(summary.getByText('忠實度低於門檻 3 筆')).toBeInTheDocument()
  expect(summary.getByText('抽取品質需複核 128 篇')).toBeInTheDocument()
  expect(summary.getByRole('link', { name: '前往待複核' })).toHaveAttribute('href', '/admin/reviews')
})

test('近 24 小時排程失敗＋fail-open → 有異常・2 項，逐條連到頁內對應的卡', async () => {
  const base = progressFixture()
  mount([progressFixture({
    unit_failures: {
      count_24h: 2, count_7d: 3, latest: '2026-10-07T12:03:44+08:00',
      recent: [
        { ts: '2026-10-07T12:03:44+08:00', unit: 'report-mark-sync.service', stage: 'import', rc: 1 },
        { ts: '2026-10-03T21:00:04+08:00', unit: 'report-mark-freshness.service', stage: 'signal', rc: null },
      ],
    },
    evaluation: { min_score: 0.9, qa: { ...base.evaluation!.qa!, degraded: 4 } },
  })])
  expect(await screen.findByText('有異常・2 項')).toBeInTheDocument()
  const list = within(screen.getByRole('list', { name: '需要處理的項目' }))
  expect(list.getByText('近 24 小時有 2 次排程失敗，最近一次 2026-10-07 12:03')).toBeInTheDocument()
  expect(list.getByRole('link', { name: '看排程與執行' })).toHaveAttribute('href', '#pipeline-schedule')
  expect(list.getByRole('link', { name: '看品質' })).toHaveAttribute('href', '#pipeline-quality')
  expect(document.getElementById('pipeline-schedule')).not.toBeNull()
  expect(document.getElementById('pipeline-quality')).not.toBeNull()
  const failures = within(screen.getByRole('table', { name: '最近的排程失敗' }))
  expect(failures.getByText('report-mark-sync.service')).toBeInTheDocument()
  expect(failures.getByText('2026-10-07 12:03')).toBeInTheDocument()
  // rc 不明（被 SIGKILL 的那一輪沒有退出碼）不猜數字
  expect(failures.getByText('不明')).toBeInTheDocument()
})

test('排程同步：狀態與原始行；沒有 log＝尚無同步紀錄；舊後端＝未提供，兩者文案不同', async () => {
  mount([progressFixture()])
  const sch = within(await screen.findByRole('region', { name: '排程與執行' }))
  expect(sch.getByText('同步已完成')).toBeInTheDocument()
  expect(sch.getByText('[2026-10-07 12:41:17] === sync done ===')).toBeInTheDocument()
  expect(sch.getByRole('link', { name: '看排程工作' })).toHaveAttribute('href', '/admin/operations/jobs')
})

test.each([
  [null, '尚無同步紀錄'],
  [undefined, '此版後端未提供同步紀錄'],
] as const)('sync=%s → %s', async (sync, text) => {
  mount([progressFixture({ sync })])
  const sch = within(await screen.findByRole('region', { name: '排程與執行' }))
  expect(sch.getByText(text)).toBeInTheDocument()
})

test('批次：只列執行中的（web 不列），其餘收成一行；沒有在跑 → 明說', async () => {
  mount([progressFixture()])
  const sch = within(await screen.findByRole('region', { name: '排程與執行' }))
  expect(sch.getByText('目前沒有批次在跑')).toBeInTheDocument()
  expect(sch.queryByText(/Web 服務/)).not.toBeInTheDocument()
  // 沒有批次在跑時不常駐空的進度列
  expect(sch.queryByText('進度')).not.toBeInTheDocument()
})

test('執行中：批次膠囊＋標註與摘要進度條；全量導入優先講篇數', async () => {
  const base = progressFixture()
  mount([progressFixture({
    pipelines: { ...base.pipelines, ingest: true, sync_import: true, tag: true, summaries: true },
    tagging: { done: 41, total: 58, fail: 1, pct: 70.7 },
    ingest: { ingested: 17, chunks: 486, fail: 0 },
  })])
  const sch = within(await screen.findByRole('region', { name: '排程與執行' }))
  const chips = within(sch.getByLabelText('執行中的批次'))
  for (const name of ['全量導入・執行中', '增量匯入・執行中', '語意標註・執行中', '摘要生成・執行中']) {
    expect(chips.getByText(name)).toBeInTheDocument()
  }
  expect(sch.getByText('本輪已導入 17 篇・失敗 0')).toBeInTheDocument()
  expect(sch.getByRole('progressbar', { name: '語意標註進度' })).toHaveAttribute('aria-valuenow', '71')
  expect(sch.getByText('70.7%・41/58・失敗 1')).toBeInTheDocument()
  expect(sch.getByRole('progressbar', { name: '摘要生成進度' })).toBeInTheDocument()
  // 速率要兩筆以上、經過 8 秒才算得出來，首筆一律是計算中
  expect(sch.getAllByText('速率 計算中…').length).toBeGreaterThan(0)
})

test('只有增量匯入在跑 → 講實話（沒有進度數字），不說「無執行中的導入」', async () => {
  const base = progressFixture()
  mount([progressFixture({ pipelines: { ...base.pipelines, sync_import: true } })])
  const sch = within(await screen.findByRole('region', { name: '排程與執行' }))
  expect(sch.getByText('增量匯入沒有進度數字')).toBeInTheDocument()
})

test('產出覆蓋：三列與最後產出；舊後端缺摘錄 → 該列降級；連到資料健康', async () => {
  mount([progressFixture({ takeaway: undefined })])
  const cov = within(await screen.findByRole('region', { name: '產出覆蓋' }))
  expect(cov.getByRole('progressbar', { name: '報告摘要覆蓋' })).toHaveAttribute('aria-valuenow', '99')
  expect(cov.getByText('2026-10-06')).toBeInTheDocument()
  expect(cov.getByText('此版後端未提供統計')).toBeInTheDocument()
  expect(cov.getByRole('link', { name: '看批次新鮮度' })).toHaveAttribute('href', '/admin/operations/data-health')
})

test('忠實度：三個訊號、門檻與待複核連結；DeepSeek 剛換尺 → 標新量尺並說明其他尺的筆數', async () => {
  const base = progressFixture()
  mount([progressFixture({
    evaluation: { min_score: 0.9, qa: { ...base.evaluation!.qa!, other_judge_checked: 88 } },
  })])
  const f = within(await screen.findByRole('region', { name: /問答忠實度/ }))
  expect(f.getByText('412')).toBeInTheDocument()
  expect(f.getByText('門檻 0.9')).toBeInTheDocument()
  expect(f.getByText('0.947')).toBeInTheDocument()
  expect(f.getByText('新量尺（自 2026-09-26 起，DeepSeek）')).toBeInTheDocument()
  expect(f.getByText(/該尺已查核 324、平均樣本數 318/)).toBeInTheDocument()
  expect(f.getByText(/另有 88 筆其他判定尺的結果只計入已查核數/)).toBeInTheDocument()
})

test('忠實度：avg 為 null → —；舊後端沒有量尺欄位 → 不印判定尺那一行', async () => {
  mount([progressFixture({
    evaluation: { min_score: 0.9, qa: { total: 10, checked: 2, degraded: 2, below_min: 0, avg_score: null, latest: null } },
  })])
  const f = within(await screen.findByRole('region', { name: /問答忠實度/ }))
  expect(f.getByText('—')).toBeInTheDocument()
  expect(f.getByText('尚無查核（門檻 0.9）')).toBeInTheDocument()
  expect(f.queryByText(/只計判定尺/)).not.toBeInTheDocument()
})

test.each([
  [undefined, '此版後端未提供查核統計'],
  [{ min_score: 0.9, qa: null }, '近 30 天沒有可查核的問答'],
] as const)('忠實度 evaluation=%o → 降級文案，卡片不消失', async (evaluation, text) => {
  mount([progressFixture({ evaluation })])
  const f = within(await screen.findByRole('region', { name: /問答忠實度/ }))
  expect(f.getByText(text)).toBeInTheDocument()
})

test('抽取：回填進度、落點分布（未知值原樣顯示）、要人看、版本', async () => {
  const base = progressFixture()
  mount([progressFixture({
    extraction: { ...base.extraction!, stopped_at: { ...base.extraction!.stopped_at, extract_error: 14, weird_state: 3 } },
  })])
  const x = within(await screen.findByRole('region', { name: /抽取品質與回填/ }))
  expect(x.getByRole('progressbar', { name: '回填進度' })).toHaveAttribute('aria-valuenow', '61')
  expect(x.getByText('已達 v4：9,928/16,220')).toBeInTheDocument()
  expect(x.getByRole('img', { name: '抽取落點分布' })).toBeInTheDocument()
  expect(x.getByText(/抽取失敗/)).toHaveTextContent('抽取失敗 14')
  expect(x.getByText(/weird_state/)).toHaveTextContent('weird_state 3')
  expect(x.getByText(/^需複核/)).toHaveTextContent('需複核 128')
  expect(x.getByText('(unknown) 6,292')).toBeInTheDocument()
})

test.each([
  [null, 'schema 尚未套用（extraction_log 不存在），請執行 make schema'],
  [undefined, '此版後端未提供抽取統計'],
] as const)('抽取 extraction=%s → 降級文案', async (extraction, text) => {
  mount([progressFixture({ extraction })])
  const x = within(await screen.findByRole('region', { name: /抽取品質與回填/ }))
  expect(x.getByText(text)).toBeInTheDocument()
})

test('語料組成：市場依篇數排序；券商摘要不計未辨識；超過 8 家可展開', async () => {
  const sources = Array.from({ length: 10 }, (_, i) => ({ source: `b${i}`, display: `券商${i}`, count: 100 - i, latest: null }))
  mount([progressFixture({ db: { ...progressFixture().db, sources: [...sources, { source: null, display: null, count: 5, latest: '2026-10-07' }] } })])
  const c = within(await screen.findByRole('region', { name: '語料組成' }))
  expect(c.getByText('16,220')).toBeInTheDocument()
  const markets = within(c.getByRole('group', { name: '市場分布' }))
  expect(markets.getByText('未分類')).toBeInTheDocument()
  expect(c.getByText('共 10 家券商・960 篇（其中未辨識 5 篇）')).toBeInTheDocument()
  const table = within(c.getByRole('table', { name: '券商分布' }))
  expect(table.getAllByRole('row')).toHaveLength(1 + 8)
  fireEvent.click(c.getByRole('button', { name: '顯示全部 11 列' }))
  expect(table.getAllByRole('row')).toHaveLength(1 + 11)
  expect(table.getByText('未辨識')).toBeInTheDocument()
  expect(c.getByRole('button', { name: '只顯示前 8 列' })).toHaveAttribute('aria-expanded', 'true')
})

test('語料組成：舊後端沒有 sources → 降級文案', async () => {
  mount([progressFixture({ db: { ...progressFixture().db, sources: undefined } })])
  const c = within(await screen.findByRole('region', { name: '語料組成' }))
  expect(c.getByText('此版後端未提供券商統計')).toBeInTheDocument()
})

test('首抓失敗 → 錯誤訊息，不白屏', async () => {
  mount([{ status: 500 }])
  expect(await screen.findByRole('alert')).toHaveTextContent('管線狀態載入失敗')
})

test('重新整理失敗時畫面保留上一筆，並標示不是最新', async () => {
  const fetchMock = mount([progressFixture(), { status: 500 }])
  expect(await screen.findByText('管線正常')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '重新整理' }))
  expect(await screen.findByText(/最近一次更新失敗/)).toBeInTheDocument()
  expect(screen.getByText('管線正常')).toBeInTheDocument()
  expect(progressCalls(fetchMock)).toBe(2)
})

test('沒有 ops.read：只顯示需要權限的說明，不打 /api/progress', async () => {
  const fetchMock = mount([progressFixture()], { scopes: ['admin'] })
  expect(await screen.findByRole('heading', { name: '需要「維運」權限' })).toBeInTheDocument()
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/me', expect.anything()))
  expect(progressCalls(fetchMock)).toBe(0)
})
