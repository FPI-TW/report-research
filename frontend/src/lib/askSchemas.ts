import { z } from 'zod'
import { rejectEvent, type RawSSEEvent } from './readSSE'

export const sourceSchema = z.object({
  n: z.number().int(),
  report_id: z.string(),
  file_name: z.string(),
  /** 報告內部標題（顯示用）；缺值＝回退 file_name。舊 qa_log 重播時本欄不存在。 */
  title: z.string().nullish(),
  market: z.string(),
  report_date: z.string().nullable().default(null),
  is_latest: z.boolean().default(false),
})
export type Source = z.infer<typeof sourceSchema>

export const extSourceSchema = z.object({ title: z.string(), url: z.string() })
export type ExtSource = z.infer<typeof extSourceSchema>

export const askStage = z.enum(['understanding', 'evaluating', 'retrieved', 'reading', 'searching_web', 'generating'])
export type AskStage = z.infer<typeof askStage>
const reportStage = z.enum(['retrieving', 'outlining', 'writing', 'searching_web', 'verifying', 'rendering'])
export type ReportStage = z.infer<typeof reportStage>

const askStatusData = z.object({ stage: askStage, count: z.number().int().optional(), thinking_ms: z.number().optional() })

// 固定婉拒的兩種來源。先前兩者共用 is_offtopic 一個布林，於是時效婉拒被渲染成
// 離題那顆警告框、附「換個說法重新提問」——對時效題那是錯的建議，換說法不會讓
// 系統生出它沒有的資料。`.catch(null)` 讓未來新增的第三種值不會讓整個 done 事件
// parse 失敗（那會讓事件回到被靜默丟棄，正是這裡要避免的事）。
export const noticeKind = z.enum(['off_topic', 'time_sensitive'])
export type NoticeKind = z.infer<typeof noticeKind>
const noticeKindField = noticeKind.nullish().catch(null)

const askDoneData = z.object({
  cited: z.array(z.string()).optional(),
  qa_id: z.string().optional(),
  conversation_id: z.string(),
  thinking_ms: z.number().optional(),
  offer_report: z.boolean().optional(),
  report_title: z.string().nullable().optional(),
  root_qa_id: z.string().nullable().optional(),
  version_count: z.number().optional(),
  notice_kind: noticeKindField,
  // 校正後的整份答案。**平時不存在**：後端只在簡體→繁體轉換確實改動了內容時才帶
  // （app/services/answer.py 的 _answer_correction）。串流的 token 是照原樣送的，
  // 而轉換是整串決定的，所以畫面靠這個欄位收斂到落庫的那一份。
  answer: z.string().optional(),
})
export type AskDone = z.infer<typeof askDoneData>
const askErrorData = z.object({ detail: z.string() })

const reportDoneData = z.object({
  report_id: z.string(),
  title: z.string(),
  download_url: z.string(),
  thinking_ms: z.number().optional(),
})
export type ReportDone = z.infer<typeof reportDoneData>

// 研報進度的三個資料來源：outline 給分母、section_draft/section_skipped 給分子。
// 後端早就在送 section_draft，前端卻沒有對應的 parser——parseReportEvent 對未知
// event 回 null，於是它一路被靜默丟棄，畫面只剩一根停在 50% 的不定量掃光條。
export const reportSectionSchema = z.object({
  position: z.number().int(),
  section_key: z.string().nullish(),
  heading: z.string(),
})
export type ReportSectionPlan = z.infer<typeof reportSectionSchema>

// sections 為空＝「忘掉大綱」（後端退單次生成時送出，見 app/services/report.py）。
const reportOutlineData = z.object({
  title: z.string().nullish(),
  sections: z.array(reportSectionSchema).catch([]),
})
// markdown 刻意不進 schema：研報內文的真相是 done 帶的 PDF，前端不渲染草稿。
const reportSectionEventData = z.object({ position: z.number().int() })
// 背景生成的 handle：重整後靠 run_id 接回，elapsed_ms 回推起始時刻（免受時鐘偏差影響）。
const reportRunData = z.object({ run_id: z.string(), elapsed_ms: z.number().default(0) })
export type ReportRunHandle = z.infer<typeof reportRunData>

// 併發滿載時，後端在真正取得名額之前先送這個（web/concurrency.py 的 ConcurrencyGate）。
// 沒有它，第 4 個之後的提問者看到的是「連線建立但永遠沒有 token」，與伺服器卡死無從分辨。
// 三個欄位全 optional：後端可能只送 scope，滾動部署期間也可能新增欄位——整包 parse
// 失敗會讓事件回到「被靜默丟棄」，那正是這裡要避免的事。
const queuedData = z.object({
  scope: z.string().optional(),
  position: z.number().int().optional(),
  capacity: z.number().int().optional(),
})
export type QueuedInfo = z.infer<typeof queuedData>

export type AskEvent =
  | { event: 'queued'; data: QueuedInfo }
  | { event: 'status'; data: z.infer<typeof askStatusData> }
  | { event: 'sources'; data: Source[] }
  | { event: 'ext_sources'; data: ExtSource[] }
  | { event: 'token'; data: string }
  | { event: 'notice'; data: string }
  | { event: 'followups'; data: string[] }
  | { event: 'done'; data: AskDone }
  | { event: 'error'; data: z.infer<typeof askErrorData> }

export type ReportEvent =
  | { event: 'run'; data: ReportRunHandle }
  | { event: 'queued'; data: QueuedInfo }
  | { event: 'status'; data: { stage: ReportStage } }
  | { event: 'outline'; data: z.infer<typeof reportOutlineData> }
  | { event: 'section_draft'; data: { position: number } }
  | { event: 'section_skipped'; data: { position: number } }
  | { event: 'sources'; data: Source[] }
  | { event: 'token'; data: string }
  | { event: 'done'; data: ReportDone }
  | { event: 'error'; data: { detail: string } }

// 丟棄事件時一律經 rejectEvent（定義在 readSSE.ts，與「JSON 壞掉」共用同一條訊息，
// 見該函式註解）。注意「靜默丟棄」精確地說只發生在 SSE 這條路徑：radar/reading 走 zod
// 但失敗會拋，`/api/progress` 是「schema 沒宣告該鍵 → 被 strip」（那要改 schema，
// 不是改 parser）。
export function parseAskEvent(raw: RawSSEEvent): AskEvent | null {
  switch (raw.event) {
    case 'queued': { const r = queuedData.safeParse(raw.data); return r.success ? { event: 'queued', data: r.data } : rejectEvent(raw.event, r.error) }
    case 'status': { const r = askStatusData.safeParse(raw.data); return r.success ? { event: 'status', data: r.data } : rejectEvent(raw.event, r.error) }
    case 'sources': { const r = z.array(sourceSchema).safeParse(raw.data); return r.success ? { event: 'sources', data: r.data } : rejectEvent(raw.event, r.error) }
    case 'ext_sources': { const r = z.array(extSourceSchema).safeParse(raw.data); return r.success ? { event: 'ext_sources', data: r.data } : rejectEvent(raw.event, r.error) }
    case 'token': return typeof raw.data === 'string' ? { event: 'token', data: raw.data } : rejectEvent(raw.event, 'data 非字串')
    case 'notice': return typeof raw.data === 'string' ? { event: 'notice', data: raw.data } : rejectEvent(raw.event, 'data 非字串')
    case 'followups': {
      const r = z.array(z.string()).safeParse(raw.data)
      return r.success ? { event: 'followups', data: r.data } : rejectEvent(raw.event, r.error)
    }
    case 'done': { const r = askDoneData.safeParse(raw.data); return r.success ? { event: 'done', data: r.data } : rejectEvent(raw.event, r.error) }
    case 'error': { const r = askErrorData.safeParse(raw.data); return r.success ? { event: 'error', data: r.data } : rejectEvent(raw.event, r.error) }
    default: return rejectEvent(raw.event, '未宣告的事件種類')
  }
}

export function parseReportEvent(raw: RawSSEEvent): ReportEvent | null {
  switch (raw.event) {
    case 'run': { const r = reportRunData.safeParse(raw.data); return r.success ? { event: 'run', data: r.data } : rejectEvent(raw.event, r.error) }
    case 'queued': { const r = queuedData.safeParse(raw.data); return r.success ? { event: 'queued', data: r.data } : rejectEvent(raw.event, r.error) }
    case 'status': { const r = z.object({ stage: reportStage }).safeParse(raw.data); return r.success ? { event: 'status', data: r.data } : rejectEvent(raw.event, r.error) }
    case 'outline': { const r = reportOutlineData.safeParse(raw.data); return r.success ? { event: 'outline', data: r.data } : rejectEvent(raw.event, r.error) }
    case 'section_draft': { const r = reportSectionEventData.safeParse(raw.data); return r.success ? { event: 'section_draft', data: r.data } : rejectEvent(raw.event, r.error) }
    case 'section_skipped': { const r = reportSectionEventData.safeParse(raw.data); return r.success ? { event: 'section_skipped', data: r.data } : rejectEvent(raw.event, r.error) }
    case 'sources': { const r = z.array(sourceSchema).safeParse(raw.data); return r.success ? { event: 'sources', data: r.data } : rejectEvent(raw.event, r.error) }
    case 'token': return typeof raw.data === 'string' ? { event: 'token', data: raw.data } : rejectEvent(raw.event, 'data 非字串')
    case 'done': { const r = reportDoneData.safeParse(raw.data); return r.success ? { event: 'done', data: r.data } : rejectEvent(raw.event, r.error) }
    case 'error': { const r = z.object({ detail: z.string() }).safeParse(raw.data); return r.success ? { event: 'error', data: r.data } : rejectEvent(raw.event, r.error) }
    // 刻意忽略，不是忘了宣告：`document_revision` 是 M7 逐節路徑的純加法事件（M7 spec
    // 設計成「舊前端可忽略」），前端真正需要的是 done 帶的 download_url——研報內文的
    // 真相是 PDF，不是串流出來的 markdown。走 default 會被 golden fixture 測試判為
    // 「後端在送、前端沒宣告」，那正是這個 case 要區分開的兩件事。
    case 'document_revision': return null
    default: return rejectEvent(raw.event, '未宣告的事件種類')
  }
}

export const conversationReportSchema = z.object({
  report_id: z.string(),
  title: z.string(),
  download_url: z.string(),
  created_at: z.string().nullish(),
})
export const conversationTurnSchema = z.object({
  id: z.string(),
  question: z.string(),
  answer: z.string(),
  created_at: z.string().nullish(),
  feedback: z.enum(['like', 'dislike']).nullable().default(null),
  sources: z.array(sourceSchema).catch([]),
  ext_sources: z.array(extSourceSchema).catch([]),
  is_offtopic: z.boolean().default(false),
  notice_kind: noticeKindField,
  thinking_ms: z.number().nullable().default(null),
  reports: z.array(conversationReportSchema).default([]),
  stages: z.array(askStage).catch([]),
  followups: z.array(z.string()).catch([]),
  root_qa_id: z.string().nullable().default(null),
  version_count: z.number().default(1),
  stopped: z.boolean().default(false),
})
export type ConversationTurn = z.infer<typeof conversationTurnSchema>

export const qaVersionSchema = z.object({
  qa_id: z.string(),
  answer: z.string(),
  sources: z.array(sourceSchema).catch([]),
  ext_sources: z.array(extSourceSchema).catch([]),
  thinking_ms: z.number().nullable().default(null),
  stages: z.array(askStage).catch([]),
  feedback: z.enum(['like', 'dislike']).nullable().default(null),
  created_at: z.string().nullish(),
})
export type QaVersion = z.infer<typeof qaVersionSchema>
