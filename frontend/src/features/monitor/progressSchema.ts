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

export const orchestratorSchema = z.object({
  raw: z.string(),
  timestamp: z.string().nullable(),
  status: z.string(),
  label: z.string(),
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
