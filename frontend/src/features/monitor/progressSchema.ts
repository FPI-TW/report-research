import { z } from 'zod'

export const marketCountSchema = z.object({ market: z.string().nullable(), count: z.number() })

/**
 * 券商分佈的一列。
 *
 * `source` 是正規化後的穩定代碼（kgi／goldman_sachs…），`display` 是後端以
 * `app/services/filename.py` 的 SOURCE_DISPLAY 對映出的中文名——**對照表刻意留在
 * 後端**，檢索頁與閱讀頁也走同一份，搬一份到前端等於兩份會漂的字典。
 * 兩者都可為 null：`source` null ＝ 檔名認不出券商（實測 327 篇），
 * `display` null 只會跟著 `source` null 一起發生（未收錄的代碼會被原樣回傳）。
 * `latest` 是該券商最新一篇的 report_date，用來看出「還在不在供稿」。
 */
export const sourceCountSchema = z.object({
  source: z.string().nullable(),
  display: z.string().nullable(),
  count: z.number(),
  latest: z.string().nullable(),
})
export const taggingSchema = z.object({ done: z.number(), total: z.number(), fail: z.number(), pct: z.number() })
export const ingestSchema = z.object({ ingested: z.number(), chunks: z.number(), fail: z.number() })
export const summarySchema = z.object({ done: z.number(), total: z.number(), remaining: z.number(), pct: z.number() })
/**
 * 各批次管線是否在跑（後端 `_proc_alive` 掃 /proc 比對 cmdline）。
 *
 * `takeaways`／`signals`／`sync_import` 用 optional 的理由同 progressSchema 下方那三塊：
 * 滾動部署期間前端可能先上線，缺鍵就整張監控頁 parse 失敗變空白，代價遠大於少一列。
 * 這個模式（後端加一格 + 這裡加一個 optional + ROWS 加一列）是新增管線列的標準作法；
 * `titles`／`sync_import` 都是照它補的。三處漏任一個都是**靜默**失效：zod 預設 strip，
 * 未宣告的鍵會被安靜丟掉，畫面上只是那一列永遠顯示「已停止」。
 *
 * `ingest` 與 `sync_import` 是**兩條不同的管線**，不要合併：前者是全量的
 * `ingest_all.py`（只在初次建庫或補跑歷史時跑），後者是生產實際的入庫路徑
 * `sync_new_reports.py`（排程每 3 小時，手動補積壓時也是它）。
 */
export const pipelinesSchema = z.object({
  web: z.boolean(),
  ingest: z.boolean(),
  sync_import: z.boolean().optional(),
  tag: z.boolean(),
  summaries: z.boolean(),
  titles: z.boolean().optional(),
  takeaways: z.boolean().optional(),
  signals: z.boolean().optional(),
  // E1d 深夜回填（backfill_extraction.py）。它不寫任何 log 檔，這格是唯一表徵。
  backfill: z.boolean().optional(),
})
/** 派生資產新鮮度：比 summary 多一個 latest（全表最新產出日）。 */
export const coverageSchema = summarySchema.extend({ latest: z.string().nullable() })

/**
 * M8 查核健康度（單一來源）。
 *
 * `checked/total` **不是覆蓋率指標**——問答端有取樣率、只查含數字的回答，
 * 研報端可停用，所以本來就不該是 100%。要看的是 degraded（judge 壞掉時
 * fail-open 的靜默累積）、below_min（待複核）與 latest（是否還在跑）。
 */
export const evalSourceSchema = z.object({
  total: z.number(),
  checked: z.number(),
  degraded: z.number(),
  below_min: z.number(),
  avg_score: z.number().nullable(),
  latest: z.string().nullable(),
})

export const evaluationSchema = z.object({
  qa: evalSourceSchema.nullable(),
  min_score: z.number(),
})

/** log 尾巴的通用形狀：原始行 + 時間戳 + 狀態 + 中文標籤（編排器與排程同步共用）。 */
export const logEntrySchema = z.object({
  raw: z.string(),
  timestamp: z.string().nullable(),
  status: z.string(),
  label: z.string(),
})
export const orchestratorSchema = logEntrySchema

/**
 * unit 失敗告警（`data/unit_failures.log`）。
 *
 * 這個檔從 P3 上線起就**零程式消費端**：2026-07-28 那次 24 小時停擺，`OnFailure`
 * 確實寫進了 10 筆，webhook 也沒設，所以整整一天沒有人知道。這裡是它的第一個出口。
 *
 * 刻意用「時間窗計數」而不是「累計未讀數」——檔案 append-only、沒有 logrotate、
 * 也沒有已讀游標，累計數當紅點條件等於永遠亮著，兩週內就會被當成背景噪音。
 */
export const unitFailureEntrySchema = z.object({
  ts: z.string().nullable(),
  unit: z.string(),
  stage: z.string().nullable(),
  rc: z.number().nullable(),
})

export const unitFailuresSchema = z.object({
  latest: z.string().nullable(),
  count_24h: z.number(),
  count_7d: z.number(),
  recent: z.array(unitFailureEntrySchema),
})

/**
 * 抽取品質與回填進度（E1，docs/EXTRACTION_REDESIGN.md §6.5）。
 *
 * `backfill` 分母是全表、分子是已達 `target_version` 者——回填要跑十幾個晚上，
 * 這是唯一不用 SQL 就看得到進度的地方。`stopped_at` 是 extraction_log 的落點分佈
 * （ingested／skip_admin／scanned／not_research／extract_error），`needs_review` 與
 * `pages_failed` 是「靜默失敗歸零」的可見面。後端缺表（schema 未套）時整塊為 null。
 */
export const extractionVersionSchema = z.object({ version: z.string(), count: z.number() })
export const extractionSchema = z.object({
  target_version: z.string(),
  versions: z.array(extractionVersionSchema),
  needs_review: z.number(),
  pages_failed: z.number(),
  stopped_at: z.record(z.string(), z.number()),
  log_latest: z.string().nullable(),
  backfill: coverageSchema,
})

export const progressSchema = z.object({
  ts: z.string(),
  db: z.object({
    reports: z.number(),
    chunks: z.number(),
    markets: z.array(marketCountSchema),
    // optional 的理由同下方三塊：滾動部署期間前端可能先上線，缺鍵就整張監控頁
    // parse 失敗變空白，代價遠大於少一張卡。
    sources: z.array(sourceCountSchema).optional(),
  }),
  summary: summarySchema,
  tagging: taggingSchema.nullable(),
  ingest: ingestSchema.nullable(),
  pipelines: pipelinesSchema,
  orchestrator: orchestratorSchema.nullable(),
  // 以下三塊 **必須用 optional**：zod 物件預設會靜默剝除未宣告的鍵，後端先前
  // 就已經在回 takeaway/signal，而這裡沒宣告 → 整路被丟掉、UI 永遠看不到。
  // 用 optional 而非必填，是為了讓「前端已更新、後端還沒」的滾動部署期間
  // 整張監控頁不會因為缺一個鍵而 parse 失敗變空白。
  takeaway: coverageSchema.optional(),
  signal: coverageSchema.optional(),
  evaluation: evaluationSchema.optional(),
  // 排程同步（每 3 小時的生產入庫路徑）與 unit 失敗告警。同樣 optional，同樣理由。
  sync: logEntrySchema.nullable().optional(),
  unit_failures: unitFailuresSchema.optional(),
  // 抽取品質（E1）。optional 同上；nullable 是後端缺表時的降級值。
  extraction: extractionSchema.nullable().optional(),
})

export type Progress = z.infer<typeof progressSchema>
export type Coverage = z.infer<typeof coverageSchema>
export type Extraction = z.infer<typeof extractionSchema>
export type Evaluation = z.infer<typeof evaluationSchema>
export type EvalSource = z.infer<typeof evalSourceSchema>
export type Tagging = z.infer<typeof taggingSchema>
export type Ingest = z.infer<typeof ingestSchema>
export type Summary = z.infer<typeof summarySchema>
export type Pipelines = z.infer<typeof pipelinesSchema>
export type MarketCount = z.infer<typeof marketCountSchema>
export type SourceCount = z.infer<typeof sourceCountSchema>
export type Orchestrator = z.infer<typeof orchestratorSchema>
export type LogEntry = z.infer<typeof logEntrySchema>
export type UnitFailures = z.infer<typeof unitFailuresSchema>
export type UnitFailureEntry = z.infer<typeof unitFailureEntrySchema>
