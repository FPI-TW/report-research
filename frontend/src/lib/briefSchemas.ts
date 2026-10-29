import { z } from 'zod'

/**
 * 每日簡報的回應契約，逐字鏡像 web/routers/brief.py 的 pydantic 模型。
 *
 * **未宣告的鍵會被 zod 安靜丟掉**（物件預設 strip，不報錯）——後端加了欄位而這裡沒加，
 * 症狀是「畫面就是沒有那塊東西」而非任何錯誤。新欄位一律 `.optional()`，讓滾動部署
 * 期間（後端已更新、瀏覽器還拿著舊 bundle，或反過來）不會整頁 parse 失敗。
 */
export const briefReportRefSchema = z.object({
  report_id: z.string(),
  file_hash: z.string(),
  file_name: z.string(),
  title: z.string().nullable().optional(),
  market: z.string().nullable().optional(),
  source: z.string().nullable().optional(),
  source_display: z.string().nullable().optional(),
  report_date: z.string().nullable().optional(),
})

// 窗期內某市場的新進研報篇數（market 為 null＝未標市場）。
export const briefMarketCountSchema = z.object({
  market: z.string().nullish(),
  report_count: z.number(),
})

export const briefPayloadSchema = z.object({
  brief_date: z.string(),
  window_start: z.string(),
  window_end: z.string(),
  markdown: z.string(),
  // 窗期內的實際篇數，可能大於 reports.length（prompt 有上限）。差額要顯示出來，
  // 否則讀者會以為那天只有這幾篇。
  report_count: z.number(),
  signal_count: z.number(),
  reports: z.array(briefReportRefSchema),
  created_at: z.string(),
  // 概況卡的市場分布與券商數：後端讀取時現算的派生資料，查詢失敗或舊版後端時缺值，
  // 概況卡就只顯示篇數。
  markets: z.array(briefMarketCountSchema).nullish(),
  broker_count: z.number().nullish(),
})

export const briefEnvelopeSchema = z.object({
  // pending＝批次還沒產生過任何一份（每天早上都會出現的正常狀態，不是錯誤）。
  status: z.enum(['ready', 'pending']),
  brief: briefPayloadSchema.nullable().optional(),
  available_dates: z.array(z.string()).optional().default([]),
})

// GET /api/brief/dates：有簡報的日期（新→舊），月曆用。
export const briefDatesSchema = z.object({
  dates: z.array(z.string()),
})

export type BriefReportRef = z.infer<typeof briefReportRefSchema>
export type BriefMarketCount = z.infer<typeof briefMarketCountSchema>
export type BriefPayload = z.infer<typeof briefPayloadSchema>
export type BriefEnvelope = z.infer<typeof briefEnvelopeSchema>
