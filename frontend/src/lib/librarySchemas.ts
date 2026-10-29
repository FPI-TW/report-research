import { z } from 'zod'
import { directionSchema, marketSchema, ratingNormSchema } from './radarSchemas'

/**
 * 研報庫首頁專用的回應契約，鏡像 app/services/radar/schemas.py 的 RatingChangesResponse。
 * 新欄位一律 `optional()`／`nullish()`：zod 預設 strip 未宣告的鍵，漏宣告只會讓畫面少一塊，不會報錯。
 */
export const ratingChangeSchema = z.object({
  market: marketSchema,
  market_display: z.string().nullish(),
  instrument_code: z.string(),
  instrument_name: z.string().nullish(),
  broker: z.string(),
  broker_display: z.string().nullish(),
  report_date: z.string(),
  rating_from: ratingNormSchema,
  rating_to: ratingNormSchema,
  direction: directionSchema,
})
export type RatingChange = z.infer<typeof ratingChangeSchema>

export const ratingChangesSchema = z.object({
  items: z.array(ratingChangeSchema),
})
export type RatingChanges = z.infer<typeof ratingChangesSchema>
