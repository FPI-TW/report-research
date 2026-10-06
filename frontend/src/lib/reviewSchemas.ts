import { z } from 'zod'

/**
 * 待複核佇列的回應契約，逐字鏡像 web/routers/review.py 的 pydantic 模型。
 *
 * 後端依 `kind` 只填其中一組欄位、其餘為 null，所以每個欄位都是 nullish。
 * 未宣告的鍵會被 zod 靜默丟掉——後端加欄位時這裡要跟著加（`optional()`）。
 */
export const reviewKindSchema = z.enum(['faithfulness', 'feedback', 'extraction'])
export type ReviewKind = z.infer<typeof reviewKindSchema>
export const reviewStatusSchema = z.enum(['open', 'resolved', 'dismissed'])
export type ReviewStatus = z.infer<typeof reviewStatusSchema>
export const reviewVerificationSchema = z.enum(['untested', 'passed', 'failed'])
export type ReviewVerification = z.infer<typeof reviewVerificationSchema>

export const reviewItemSchema = z.object({
  // qa 列只有中繼資料：佇列刻意不回提問原文、回答、提問者帳號與對話串（後端去內容化）。
  qa_id: z.string().nullish(),
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
  // extraction 列：app/services/store.py 的 REVIEW_REASONS。刻意收成 string 而非 enum：後端新增一種原因時，
  // enum 會讓整頁 parse 失敗；收成 string，畫面只是多一個沒有中文標籤的代碼。
  review_reasons: z.array(z.string()).nullish(),
  review_status: reviewStatusSchema.optional(),
  review_note: z.string().optional(),
  verification: reviewVerificationSchema.optional(),
  reviewed_at: z.string().nullish(),
  // 最後處理人（管理員帳號名）。共用帳號時期的舊資料是 null。
  reviewer: z.string().nullish(),
  // 問答的提問者代號：不可逆短代號，同一人同一代號、看不出是誰。共用帳號時期的舊提問是 null。
  asker_code: z.string().nullish(),
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

export const reviewStateSchema = z.object({
  kind: reviewKindSchema,
  subject_id: z.string(),
  status: reviewStatusSchema,
  note: z.string(),
  verification: reviewVerificationSchema,
  updated_at: z.string(),
  reviewer: z.string().nullish(),
})
