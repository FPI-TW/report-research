import { z } from 'zod'

export const marketCountSchema = z.object({ market: z.string().nullable(), count: z.number() })
export const taggingSchema = z.object({ done: z.number(), total: z.number(), fail: z.number(), pct: z.number() })
export const ingestSchema = z.object({ ingested: z.number(), chunks: z.number(), fail: z.number() })
export const summarySchema = z.object({ done: z.number(), total: z.number(), remaining: z.number(), pct: z.number() })
export const pipelinesSchema = z.object({ web: z.boolean(), ingest: z.boolean(), tag: z.boolean(), summaries: z.boolean() })
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
  report: evalSourceSchema.nullable(),
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

export const progressSchema = z.object({
  ts: z.string(),
  db: z.object({
    reports: z.number(),
    chunks: z.number(),
    markets: z.array(marketCountSchema),
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
})

export type Progress = z.infer<typeof progressSchema>
export type Coverage = z.infer<typeof coverageSchema>
export type Evaluation = z.infer<typeof evaluationSchema>
export type EvalSource = z.infer<typeof evalSourceSchema>
export type Tagging = z.infer<typeof taggingSchema>
export type Ingest = z.infer<typeof ingestSchema>
export type Summary = z.infer<typeof summarySchema>
export type Pipelines = z.infer<typeof pipelinesSchema>
export type MarketCount = z.infer<typeof marketCountSchema>
export type Orchestrator = z.infer<typeof orchestratorSchema>
export type LogEntry = z.infer<typeof logEntrySchema>
export type UnitFailures = z.infer<typeof unitFailuresSchema>
export type UnitFailureEntry = z.infer<typeof unitFailureEntrySchema>
