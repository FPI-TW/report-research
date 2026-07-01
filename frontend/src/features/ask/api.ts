import { z } from 'zod'
import { ApiError, getJSON, redirectToLogin } from '../../lib/api'
import { conversationSummarySchema, historyItemSchema, reportFullSchema, type ConversationSummary, type HistoryItem, type ReportFull } from './schemas'

export function getConversations(): Promise<ConversationSummary[]> {
  return getJSON('/api/conversations?limit=50', z.array(conversationSummarySchema), { cache: 'no-store' })
}

export function getConversation(id: string): Promise<HistoryItem[]> {
  return getJSON(`/api/conversations/${encodeURIComponent(id)}`, z.array(historyItemSchema))
}

/** DELETE /api/conversations/{id}；404/405 退回 POST /delete（移植 ask.js:231-236）。 */
export async function deleteConversation(id: string): Promise<{ ok: boolean }> {
  const path = `/api/conversations/${encodeURIComponent(id)}`
  let resp = await fetch(path, { method: 'DELETE', credentials: 'same-origin' })
  if (resp.status === 404 || resp.status === 405) {
    resp = await fetch(`${path}/delete`, { method: 'POST', credentials: 'same-origin' })
  }
  if (resp.status === 401) {
    redirectToLogin()
    throw new ApiError(401, '未登入')
  }
  const data = (await resp.json().catch(() => ({}))) as { ok?: boolean }
  if (!resp.ok || !data.ok) throw new ApiError(resp.status, '刪除失敗')
  return { ok: true }
}

/** 回饋為 best-effort，失敗不打擾使用者（移植 ask.js:556-568）。 */
export async function sendFeedback(qaId: string, value: 'like' | 'dislike'): Promise<void> {
  try {
    await fetch('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ qa_id: qaId, value }),
    })
  } catch {
    /* 忽略 */
  }
}

export async function getReportFull(id: string): Promise<ReportFull> {
  return getJSON(`/api/report/${encodeURIComponent(id)}/full`, reportFullSchema)
}
