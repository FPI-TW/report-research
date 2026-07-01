import { z } from 'zod'

export const sourceSchema = z.object({
  n: z.number().int(),
  report_id: z.string(),
  file_name: z.string(),
  market: z.string().nullish(),
  report_date: z.string().nullish(),
  is_latest: z.boolean().nullish(),
})

export const extSourceSchema = z.object({
  title: z.string().nullish(),
  url: z.string(),
})

export const conversationSummarySchema = z.object({
  conversation_id: z.string(),
  title: z.string(),
  last_at: z.string().nullish(),
  turn_count: z.number().int().nonnegative().nullish(),
})
export type ConversationSummary = z.infer<typeof conversationSummarySchema>

export const kpiItemSchema = z.object({
  label: z.string().default(''),
  value: z.string().default(''),
  change: z.string().nullish(),
  dir: z.enum(['up', 'down']).nullish(),
  source: z.string().nullish(),
})

export const kpiBlockSchema = z.object({ items: z.array(kpiItemSchema).default([]) })
export type KpiBlock = z.infer<typeof kpiBlockSchema>

export const chartSeriesSchema = z.object({ name: z.string().default(''), values: z.array(z.number()) })

export const chartBlockSchema = z.object({
  type: z.enum(['bar', 'line', 'pie']),
  title: z.string().nullish(),
  x: z.array(z.union([z.string(), z.number()])).default([]),
  series: z.array(chartSeriesSchema).default([]),
  unit: z.string().nullish(),
  source: z.string().nullish(),
})
export type ChartBlock = z.infer<typeof chartBlockSchema>

export const reportSummarySchema = z.object({
  report_id: z.string(),
  title: z.string().nullish(),
  download_url: z.string(),
  created_at: z.string().nullish(),
})
export type ReportSummary = z.infer<typeof reportSummarySchema>

export const reportFullSchema = z.object({
  report_id: z.string(),
  title: z.string().nullish(),
  markdown: z.string().default(''),
})
export type ReportFull = z.infer<typeof reportFullSchema>

export const historyItemSchema = z.object({
  id: z.string(),
  question: z.string(),
  answer: z.string().nullish(),
  created_at: z.string().nullish(),
  feedback: z.string().nullish(),
  sources: z.array(sourceSchema).nullish(),
  ext_sources: z.array(extSourceSchema).nullish(),
  is_offtopic: z.boolean().nullish(),
  thinking_ms: z.number().nullish(),
  reports: z.array(reportSummarySchema).nullish(),
})
export type HistoryItem = z.infer<typeof historyItemSchema>

export const okSchema = z.object({ ok: z.boolean() })
