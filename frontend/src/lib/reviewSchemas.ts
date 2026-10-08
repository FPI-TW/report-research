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

/**
 * 待複核頁的判定尺（`GET /api/review/judge-scale`，`review.manage`），逐字鏡像 web/routers/review.py 的
 * `JudgeScaleResponse`：`/api/progress` 的 `evaluation.qa` 裡標「新量尺」需要的三個鍵。
 * 待複核頁刻意不讀 `/api/progress`：那支要 `ops.read`，而且帶整份 runtime 與管線資料。
 */
export const judgeScaleSchema = z.object({
  judge_model: z.string(),
  judge_since: z.string().nullable(),
  other_judge_checked: z.number(),
})
export type JudgeScale = z.infer<typeof judgeScaleSchema>

/**
 * 逐筆讀取的一筆問答原文（`POST /api/review/qa/{qa_id}/access`，要 `qa_content.read`）。
 * 只有這一筆，不含對話串；每次讀取後端都寫一列稽核。
 */
export const qaContentSchema = z.object({
  qa_id: z.string(),
  kinds: z.array(reviewKindSchema),
  created_at: z.string().nullish(),
  question: z.string(),
  answer: z.string().nullish(),
})
export type QaContent = z.infer<typeof qaContentSchema>
