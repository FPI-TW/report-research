import { z } from 'zod'

export const statsSchema = z.object({
  total_reports: z.number(),
  total_chunks: z.number(),
  markets: z.array(z.object({ market: z.string(), count: z.number() })),
  instrument_types: z.array(z.object({ type: z.string(), count: z.number() })),
  report_types: z.array(z.object({ type: z.string(), count: z.number() })),
  username: z.string().nullish(),
})
export type StatsResponse = z.infer<typeof statsSchema>

export const reportItemSchema = z.object({
  report_id: z.string(),
  file_name: z.string(),
  market: z.string().nullish(),
  source: z.string().nullish(),
  summary: z.string().nullish(),
  report_date: z.string().nullish(),
  report_type: z.string().nullish(),
  instrument_types: z.array(z.string()).nullish(),
  relates_stock: z.boolean().nullish(),
  relates_futures: z.boolean().nullish(),
  stock_targets: z.array(z.string()).nullish(),
  futures_targets: z.array(z.string()).nullish(),
})
export type ReportItem = z.infer<typeof reportItemSchema>

export const reportsSchema = z.object({
  total: z.number(),
  offset: z.number(),
  items: z.array(reportItemSchema),
})
export type ReportsResponse = z.infer<typeof reportsSchema>

export const passageSchema = z.object({
  score: z.number(),
  chunk_index: z.number(),
  content: z.string(),
})

export const reportResultSchema = reportItemSchema.extend({
  rank: z.number(),
  best_score: z.number(),
  match_count: z.number(),
  passages: z.array(passageSchema),
})
export type ReportResult = z.infer<typeof reportResultSchema>

export const searchSchema = z.object({
  query: z.string(),
  market: z.string().nullish(),
  total: z.number(),
  results: z.array(reportResultSchema),
})
export type SearchResponse = z.infer<typeof searchSchema>
