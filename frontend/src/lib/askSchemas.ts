import { z } from 'zod'
import type { RawSSEEvent } from './readSSE'

export const sourceSchema = z.object({
  n: z.number().int(),
  report_id: z.string(),
  file_name: z.string(),
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
const askDoneData = z.object({
  cited: z.array(z.string()).optional(),
  qa_id: z.string().optional(),
  conversation_id: z.string(),
  thinking_ms: z.number().optional(),
  offer_report: z.boolean().optional(),
  report_title: z.string().nullable().optional(),
  root_qa_id: z.string().nullable().optional(),
  version_count: z.number().optional(),
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

export function parseAskEvent(raw: RawSSEEvent): AskEvent | null {
  switch (raw.event) {
    case 'queued': { const r = queuedData.safeParse(raw.data); return r.success ? { event: 'queued', data: r.data } : null }
    case 'status': { const r = askStatusData.safeParse(raw.data); return r.success ? { event: 'status', data: r.data } : null }
    case 'sources': { const r = z.array(sourceSchema).safeParse(raw.data); return r.success ? { event: 'sources', data: r.data } : null }
    case 'ext_sources': { const r = z.array(extSourceSchema).safeParse(raw.data); return r.success ? { event: 'ext_sources', data: r.data } : null }
    case 'token': return typeof raw.data === 'string' ? { event: 'token', data: raw.data } : null
    case 'notice': return typeof raw.data === 'string' ? { event: 'notice', data: raw.data } : null
    case 'followups': {
      const r = z.array(z.string()).safeParse(raw.data)
      return r.success ? { event: 'followups', data: r.data } : null
    }
    case 'done': { const r = askDoneData.safeParse(raw.data); return r.success ? { event: 'done', data: r.data } : null }
    case 'error': { const r = askErrorData.safeParse(raw.data); return r.success ? { event: 'error', data: r.data } : null }
    default: return null
  }
}

export function parseReportEvent(raw: RawSSEEvent): ReportEvent | null {
  switch (raw.event) {
    case 'run': { const r = reportRunData.safeParse(raw.data); return r.success ? { event: 'run', data: r.data } : null }
    case 'queued': { const r = queuedData.safeParse(raw.data); return r.success ? { event: 'queued', data: r.data } : null }
    case 'status': { const r = z.object({ stage: reportStage }).safeParse(raw.data); return r.success ? { event: 'status', data: r.data } : null }
    case 'outline': { const r = reportOutlineData.safeParse(raw.data); return r.success ? { event: 'outline', data: r.data } : null }
    case 'section_draft': { const r = reportSectionEventData.safeParse(raw.data); return r.success ? { event: 'section_draft', data: r.data } : null }
    case 'section_skipped': { const r = reportSectionEventData.safeParse(raw.data); return r.success ? { event: 'section_skipped', data: r.data } : null }
    case 'sources': { const r = z.array(sourceSchema).safeParse(raw.data); return r.success ? { event: 'sources', data: r.data } : null }
    case 'token': return typeof raw.data === 'string' ? { event: 'token', data: raw.data } : null
    case 'done': { const r = reportDoneData.safeParse(raw.data); return r.success ? { event: 'done', data: r.data } : null }
    case 'error': { const r = z.object({ detail: z.string() }).safeParse(raw.data); return r.success ? { event: 'error', data: r.data } : null }
    default: return null
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
