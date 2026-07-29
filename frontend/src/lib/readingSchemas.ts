import { z } from 'zod'

/**
 * 與後端 app/services/reading/schemas.py 逐字鏡像的契約。
 *
 * 狀態詞彙刻意與 DB 分離：DB 的 extraction_status 記錄「批次做了什麼」；
 * 此處的 *_state 記錄「讀者拿到什麼」。兩者不可混用。
 */

/** 讀者視角狀態（非 DB 狀態）。 */
export const textStateSchema = z.enum(['ok', 'missing'])
export type TextState = z.infer<typeof textStateSchema>

export const signalsStateSchema = z.enum(['available', 'none'])
export type SignalsState = z.infer<typeof signalsStateSchema>

export const anchorMethodSchema = z.enum(['exact', 'normalized', 'prefix'])
export type AnchorMethod = z.infer<typeof anchorMethodSchema>

export const readingRatingNormSchema = z.enum([
  'buy', 'overweight', 'neutral', 'underweight', 'sell', 'unknown',
])
export type ReadingRatingNorm = z.infer<typeof readingRatingNormSchema>

export const thesisKeySchema = z.enum(['outlook', 'catalyst', 'risk', 'valuation'])
export type ThesisKey = z.infer<typeof thesisKeySchema>

/**
 * 一條重點摘錄。quote_start 為 null＝不可跳，前端顯示條目但不給跳轉。
 *
 * null 的成因（錨不到／驗章不過／落在 /text 的截斷範圍外）全由後端判斷並收回，
 * 前端不重造這個判斷 —— 兩邊各自判會分岔成「顯示可點、點下去卻找不到錨點」。
 */
export const takeawaySchema = z.object({
  ordinal: z.number().int(),
  claim: z.string(),
  quote: z.string().nullish(),
  quote_start: z.number().int().nullish(),
  quote_end: z.number().int().nullish(),
  anchor_method: anchorMethodSchema.nullish(),
})
export type Takeaway = z.infer<typeof takeawaySchema>

export const thesisDimSchema = z.object({
  key: thesisKeySchema,
  stance: z.string().nullish(),
  summary: z.string().nullish(),
  evidence: z.string().nullish(),
})
export type ThesisDim = z.infer<typeof thesisDimSchema>

export const epsEstimateSchema = z.object({
  // 後端此處為 Optional[str]（與 radar 的 int 不同，勿混用）
  fiscal_year: z.string().nullish(),
  period: z.string().nullish(),
  currency: z.string().nullish(),
  unit: z.string().nullish(),
  value: z.number().nullish(),
})
export type EpsEstimate = z.infer<typeof epsEstimateSchema>

/** 一份研報對一個標的的結構化訊號。全語料僅 0.68% 的報告有。 */
export const signalSchema = z.object({
  instrument_code: z.string(),
  market: z.string(),
  broker: z.string().nullish(),
  broker_display: z.string().nullish(),
  rating_raw: z.string().nullish(),
  rating_normalized: readingRatingNormSchema.default('unknown'),
  target_price: z.number().nullish(),
  target_currency: z.string().nullish(),
  target_horizon: z.string().nullish(),
  eps_estimates: z.array(epsEstimateSchema).default([]),
  thesis: z.array(thesisDimSchema).default([]),
})
export type Signal = z.infer<typeof signalSchema>

/** 閱讀頁骨架。不含全文 —— PDF 是預設檢視，用不到；全文另走 /text。 */
export const readingDocSchema = z.object({
  report_id: z.string(),
  file_hash: z.string(),
  file_name: z.string(),
  /** 報告內部標題（頁首顯示用）；缺值＝批次尚未產生，回退 file_name。 */
  title: z.string().nullish(),
  market: z.string().nullish(),
  source: z.string().nullish(),
  source_display: z.string().nullish(),
  report_date: z.string().nullish(),
  report_type: z.string().nullish(),
  summary: z.string().nullish(),
  instrument_types: z.array(z.string()).default([]),
  stock_targets: z.array(z.string()).default([]),
  futures_targets: z.array(z.string()).default([]),
  has_file: z.boolean().default(false),
  is_pdf: z.boolean().default(false),

  text_state: textStateSchema.default('missing'),
  text_chars: z.number().int().default(0),
  /** sha256(clean_extracted(full_text))。只在與 /text 回傳相符時才啟用引文跳轉。 */
  text_sha256: z.string().nullish(),

  takeaways: z.array(takeawaySchema).default([]),
  signals_state: signalsStateSchema.default('none'),
  signals: z.array(signalSchema).default([]),
})
export type ReadingDoc = z.infer<typeof readingDocSchema>

/** 正典文字＝clean_extracted(full_text)。所有 offset 都以此字串為準。 */
export const readingTextSchema = z.object({
  file_hash: z.string(),
  text: z.string(),
  text_sha256: z.string(),
  text_chars: z.number().int(),
  truncated: z.boolean().default(false),
  /**
   * 檢索命中段的字元區間；僅當抓取帶 ?chunk= 且該段錨定成功時有值。
   * 錨不到＝null → 前端不高亮，但頁面照常（不是錯誤）。
   * 不需驗章：與同一回應的 text 出自同一份正典文字，必然同源。
   */
  chunk_start: z.number().int().nullish(),
  chunk_end: z.number().int().nullish(),
})
export type ReadingText = z.infer<typeof readingTextSchema>

export const similarReportSchema = z.object({
  file_hash: z.string(),
  file_name: z.string(),
  title: z.string().nullish(),
  market: z.string().nullish(),
  source: z.string().nullish(),
  source_display: z.string().nullish(),
  report_date: z.string().nullish(),
  summary: z.string().nullish(),
  /** 「9/12 段相符」的兩個數字（設計稿拍板的說法）。 */
  matched_probes: z.number().int(),
  total_probes: z.number().int(),
})
export type SimilarReport = z.infer<typeof similarReportSchema>

export const similarResponseSchema = z.object({
  file_hash: z.string(),
  items: z.array(similarReportSchema).default([]),
})
export type SimilarResponse = z.infer<typeof similarResponseSchema>
