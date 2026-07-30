import { z } from 'zod'

export const statsSchema = z.object({
  total_reports: z.number().int().nonnegative(),
  total_chunks: z.number().int().nonnegative(),
  markets: z.array(z.object({ market: z.string(), count: z.number().int().nonnegative() })),
  instrument_types: z.array(z.object({ type: z.string(), count: z.number().int().nonnegative() })),
  report_types: z.array(z.object({ type: z.string(), count: z.number().int().nonnegative() })),
  username: z.string().nullish(),
})
export type StatsResponse = z.infer<typeof statsSchema>

export const conversationSummarySchema = z.object({
  conversation_id: z.string(),
  title: z.string(),
  last_at: z.string().nullish(),
  turn_count: z.number().int().nonnegative().nullish(),
})
export type ConversationSummary = z.infer<typeof conversationSummarySchema>

export const passageSchema = z.object({
  score: z.number(),
  chunk_index: z.number().int(),
  content: z.string(),
})
export type Passage = z.infer<typeof passageSchema>

export const reportListItemSchema = z.object({
  report_id: z.string(),
  /** 閱讀頁 /report/:hash 的鍵（原始檔仍走 report_id）。 */
  file_hash: z.string(),
  file_name: z.string(),
  /**
   * 報告內部標題（顯示用）。null/undefined＝批次尚未產生，一律以 displayTitle()
   * 回退 file_name。zod 預設會靜默 strip 未宣告的鍵，漏宣告＝後端送了也進不了 DOM。
   */
  title: z.string().nullish(),
  market: z.string().nullable(),
  source: z.string().nullable(),
  summary: z.string().nullable(),
  report_date: z.string().nullable(),
  report_type: z.string().nullable(),
  instrument_types: z.array(z.string()).nullable(),
  relates_stock: z.boolean().nullable(),
  relates_futures: z.boolean().nullable(),
  stock_targets: z.array(z.string()).nullable(),
  futures_targets: z.array(z.string()).nullable(),
})
export type ReportListItem = z.infer<typeof reportListItemSchema>

export const reportResultSchema = reportListItemSchema.extend({
  rank: z.number().int(),
  best_score: z.number(),
  match_count: z.number().int(),
  passages: z.array(passageSchema),
})
export type ReportResult = z.infer<typeof reportResultSchema>

export const reportListResponseSchema = z.object({
  total: z.number().int(),
  offset: z.number().int(),
  items: z.array(reportListItemSchema),
})
export type ReportListResponse = z.infer<typeof reportListResponseSchema>

export const marketFacetSchema = z.object({
  market: z.string(),
  count: z.number().int(),
})
export type MarketFacet = z.infer<typeof marketFacetSchema>

export const searchResponseSchema = z.object({
  query: z.string(),
  market: z.string().nullable(),
  total: z.number().int(),
  /** 命中集合的市場組成（色譜讀數）。舊後端無此欄，故給預設空陣列。 */
  market_facets: z.array(marketFacetSchema).default([]),
  /**
   * 字面路候選是否已被 cap 截斷（`store._lexical_sql` 的 `LIMIT :cap`，沒有 ORDER BY
   * ⇒ 取到哪 cap 列由 heap 物理順序決定，同一查詢在不同時刻可能回不同結果）。
   *
   * 刻意用 `optional()` 而非 `default(false)`：undefined＝這個後端還沒回報（滾動部署
   * 期間的舊版），false＝回報了且沒截斷。兩者是不同的事實，混成同一個值就再也分不開。
   * 宣告本身是必要的——zod 物件預設 `strip`，未宣告的鍵不報錯、直接安靜丟掉。
   */
  lexical_truncated: z.boolean().optional(),
  results: z.array(reportResultSchema),
})
export type SearchResponse = z.infer<typeof searchResponseSchema>

export const reportFullSchema = z.object({
  report_id: z.string(),
  file_name: z.string(),
  title: z.string().nullish(),
  market: z.string().nullable(),
  source: z.string().nullable(),
  summary: z.string().nullable(),
  report_date: z.string().nullable(),
  report_type: z.string().nullable(),
  has_file: z.boolean(),
})
export type ReportFull = z.infer<typeof reportFullSchema>

/** browse item 與 search result 的統一列型別：browse 時 rank/best_score/match_count/passages 為 undefined。 */
export type ReportRow = ReportListItem & {
  rank?: number
  best_score?: number
  match_count?: number
  passages?: Passage[]
}
