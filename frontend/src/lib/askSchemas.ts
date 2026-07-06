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

const askStage = z.enum(['understanding', 'retrieved', 'reading', 'searching_web', 'generating'])
export type AskStage = z.infer<typeof askStage>
const reportStage = z.enum(['retrieving', 'writing', 'searching_web', 'rendering'])
export type ReportStage = z.infer<typeof reportStage>

const askStatusData = z.object({ stage: askStage, count: z.number().int().optional(), thinking_ms: z.number().optional() })
const askDoneData = z.object({
  cited: z.array(z.string()).optional(),
  qa_id: z.string().optional(),
  conversation_id: z.string(),
  thinking_ms: z.number().optional(),
  offer_report: z.boolean().optional(),
  report_title: z.string().nullable().optional(),
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

export type AskEvent =
  | { event: 'status'; data: z.infer<typeof askStatusData> }
  | { event: 'sources'; data: Source[] }
  | { event: 'ext_sources'; data: ExtSource[] }
  | { event: 'token'; data: string }
  | { event: 'notice'; data: string }
  | { event: 'done'; data: AskDone }
  | { event: 'error'; data: z.infer<typeof askErrorData> }

export type ReportEvent =
  | { event: 'status'; data: { stage: ReportStage } }
  | { event: 'sources'; data: Source[] }
  | { event: 'token'; data: string }
  | { event: 'done'; data: ReportDone }
  | { event: 'error'; data: { detail: string } }

export function parseAskEvent(raw: RawSSEEvent): AskEvent | null {
  switch (raw.event) {
    case 'status': { const r = askStatusData.safeParse(raw.data); return r.success ? { event: 'status', data: r.data } : null }
    case 'sources': { const r = z.array(sourceSchema).safeParse(raw.data); return r.success ? { event: 'sources', data: r.data } : null }
    case 'ext_sources': { const r = z.array(extSourceSchema).safeParse(raw.data); return r.success ? { event: 'ext_sources', data: r.data } : null }
    case 'token': return typeof raw.data === 'string' ? { event: 'token', data: raw.data } : null
    case 'notice': return typeof raw.data === 'string' ? { event: 'notice', data: raw.data } : null
    case 'done': { const r = askDoneData.safeParse(raw.data); return r.success ? { event: 'done', data: r.data } : null }
    case 'error': { const r = askErrorData.safeParse(raw.data); return r.success ? { event: 'error', data: r.data } : null }
    default: return null
  }
}

export function parseReportEvent(raw: RawSSEEvent): ReportEvent | null {
  switch (raw.event) {
    case 'status': { const r = z.object({ stage: reportStage }).safeParse(raw.data); return r.success ? { event: 'status', data: r.data } : null }
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
})
export type ConversationTurn = z.infer<typeof conversationTurnSchema>
