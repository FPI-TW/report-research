import { z } from 'zod'

export const statsSchema = z.object({
  total_reports: z.number().int().nonnegative(),
  total_chunks: z.number().int().nonnegative(),
  markets: z.array(z.object({ market: z.string(), count: z.number().int().nonnegative() })),
  instrument_types: z.array(
    z.object({ type: z.string(), count: z.number().int().nonnegative() }),
  ),
  report_types: z.array(z.object({ type: z.string(), count: z.number().int().nonnegative() })),
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
  total: z.number().int().nonnegative(),
  offset: z.number().int().nonnegative(),
  items: z.array(reportItemSchema),
})
export type ReportsResponse = z.infer<typeof reportsSchema>

export const passageSchema = z.object({
  score: z.number(),
  chunk_index: z.number().int().nonnegative(),
  content: z.string(),
})
export type Passage = z.infer<typeof passageSchema>

export const reportResultSchema = reportItemSchema.extend({
  rank: z.number().int().positive(),
  best_score: z.number(),
  match_count: z.number().int().nonnegative(),
  passages: z.array(passageSchema),
})
export type ReportResult = z.infer<typeof reportResultSchema>

export const searchSchema = z.object({
  query: z.string(),
  market: z.string().nullish(),
  total: z.number().int().nonnegative(),
  results: z.array(reportResultSchema),
})
export type SearchResponse = z.infer<typeof searchSchema>
