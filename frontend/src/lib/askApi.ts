import { z } from 'zod'
import { getJSON } from './api'
import { readSSE, type RawSSEEvent } from './readSSE'
import { conversationTurnSchema, qaVersionSchema, type ConversationTurn, type QaVersion } from './askSchemas'

export function streamAsk(
  body: { question: string; conversation_id?: string; regenerate_of?: string; edit_of?: string; request_id?: string },
  signal: AbortSignal,
): AsyncGenerator<RawSSEEvent> {
  return readSSE('/api/ask', body, signal)
}

export async function stopAsk(body: {
  question: string
  conversation_id?: string | null
  partial_answer: string
  sources?: unknown[]
  ext_sources?: unknown[]
  stages?: string[]
  regenerate_of?: string
  request_id?: string
}): Promise<{ qa_id: string }> {
  const resp = await fetch('/api/ask/stop', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    credentials: 'same-origin',
  })
  if (!resp.ok) throw new Error(`stop failed: ${resp.status}`)
  return resp.json()
}

export function getQaVersions(rootId: string): Promise<QaVersion[]> {
  return getJSON(`/api/qa/${encodeURIComponent(rootId)}/versions`, z.array(qaVersionSchema), { cache: 'no-store' })
}

export function streamReport(body: { question: string; conversation_id?: string; qa_id?: string; template_id?: string }, signal: AbortSignal): AsyncGenerator<RawSSEEvent> {
  return readSSE('/api/report', body, signal)
}

// M9b：可選研報渲染模板（registry）。前端模板選擇器資料源。
export const reportTemplateSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  is_default: z.boolean(),
  thumbnail: z.string().nullable(),
})
export type ReportTemplate = z.infer<typeof reportTemplateSchema>

export function getReportTemplates(): Promise<ReportTemplate[]> {
  return getJSON('/api/report-templates', z.object({ templates: z.array(reportTemplateSchema) }), { cache: 'no-store' })
    .then((r) => r.templates)
}

export function getConversation(id: string): Promise<ConversationTurn[]> {
  return getJSON(`/api/conversations/${encodeURIComponent(id)}`, z.array(conversationTurnSchema), { cache: 'no-store' })
}

export async function deleteConversation(id: string): Promise<void> {
  const path = `/api/conversations/${encodeURIComponent(id)}`
  const resp = await fetch(path, { method: 'DELETE', credentials: 'same-origin' })
  if (resp.status === 404 || resp.status === 405) {
    await fetch(`${path}/delete`, { method: 'POST', credentials: 'same-origin' })
  }
}

export async function sendFeedback(qaId: string, value: 'like' | 'dislike'): Promise<void> {
  await fetch('/api/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ qa_id: qaId, value }),
    credentials: 'same-origin',
  })
}
