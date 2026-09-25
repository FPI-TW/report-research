import { z } from 'zod'

/**
 * 待複核佇列的回應契約，逐字鏡像 web/routers/review.py 的 pydantic 模型。
 *
 * 後端依 `kind` 只填其中一組欄位、其餘為 null，所以每個欄位都是 nullish。
 * 未宣告的鍵會被 zod 靜默丟掉——後端加欄位時這裡要跟著加（`optional()`）。
 */
export const reviewKindSchema = z.enum(['faithfulness', 'feedback', 'extraction'])
export type ReviewKind = z.infer<typeof reviewKindSchema>

export const reviewItemSchema = z.object({
  qa_id: z.string().nullish(),
  conversation_id: z.string().nullish(),
  question: z.string().nullish(),
  created_at: z.string().nullish(),
  faithfulness_score: z.number().nullish(),
  feedback: z.string().nullish(),
  report_id: z.string().nullish(),
  file_hash: z.string().nullish(),
  file_name: z.string().nullish(),
  title: z.string().nullish(),
  source: z.string().nullish(),
  report_date: z.string().nullish(),
  quality_score: z.number().nullish(),
  quality_flags: z.record(z.string(), z.unknown()).nullish(),
  pages_failed: z.array(z.number()).nullish(),
  // qa 列的 evaluation 是哪個 judge 量的（舊列＝claude-haiku-4-5；沒有 evaluation＝null）。
  judge_model: z.string().nullish(),
})
export type ReviewItem = z.infer<typeof reviewItemSchema>

export const reviewQueueSchema = z.object({
  kind: reviewKindSchema,
  total: z.number(),
  limit: z.number(),
  offset: z.number(),
  has_more: z.boolean(),
  next_offset: z.number().nullable(),
  min_score: z.number().nullish(),
  items: z.array(reviewItemSchema),
})
export type ReviewQueue = z.infer<typeof reviewQueueSchema>
