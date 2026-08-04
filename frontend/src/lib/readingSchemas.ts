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
 * 一條重點摘錄。條目與逐字引文一律顯示；quote_start 為 null＝錨點無效。
 *
 * null 的成因（錨不到／驗章不過／落在 /text 的截斷範圍外）全由後端判斷並收回，
 * 前端不重造這個判斷 —— **錨點有效與否只該有一個真相來源**。
 *
 * 三個 offset 欄位**目前沒有任何前端消費端**（引文跳轉已於 2026-08-03 隨文字檢視
 * 移除），保留宣告是為了後端契約完整與日後恢復的可逆性，不是遺漏。
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

/**
 * 一份研報對一個標的的結構化訊號。全語料僅 0.68% 的報告有。
 *
 * 一份研報可能同時對多檔標的有訊號，所以**代號是辨識這張卡在講誰的唯一依據**。
 * instrument_name 由後端另查 research_report.company_name 而來，解析不出公司名的標的
 * 為 null＝常態不是錯誤，呈現層回退成只顯示代號（比照 title → file_name）。
 */
export const signalSchema = z.object({
  instrument_code: z.string(),
  instrument_name: z.string().nullish(),
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

/** 閱讀頁骨架。不含全文 —— 絕大多數研報直接內嵌 PDF，用不到；全文另走 /text。 */
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
  /**
   * sha256(clean_extracted(full_text))。前端目前不消費（引文跳轉已移除）——
   * 保留宣告是因為後端仍在回，且它是「摘錄與全文是否同源」的唯一驗章依據，
   * 日後要恢復跳轉就要靠它。
   */
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
  // 後端仍會回 chunk_start/chunk_end（僅在帶 ?chunk= 時有值），但前端不再帶那個參數、
  // 也沒有命中高亮的落點，故刻意不宣告 —— zod 預設 strip，多回的鍵會被安靜丟掉。
  // 注意這個 strip 是雙面刃：日後後端**新增**的欄位同樣會被安靜丟掉，加欄位時要記得
  // 同步在這裡宣告（用 optional()，滾動部署才不會整頁 parse 失敗）。
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
