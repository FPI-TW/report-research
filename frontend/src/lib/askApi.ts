import { z } from 'zod'
import { getJSON } from './api'
import { readSSE, type RawSSEEvent } from './readSSE'
import { conversationTurnSchema, qaVersionSchema, type ConversationTurn, type QaVersion } from './askSchemas'
import type { Locale } from './useLocale'

export function streamAsk(
  body: { question: string; conversation_id?: string; regenerate_of?: string; edit_of?: string; request_id?: string; locale?: Locale },
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

export function streamReport(body: { question: string; conversation_id?: string; qa_id?: string; template_id?: string; locale?: Locale }, signal: AbortSignal): AsyncGenerator<RawSSEEvent> {
  return readSSE('/api/report', body, signal)
}

// ── 背景研報 run（重整/開新分頁後接回進度）─────────────────────────────────
// 生成跑在伺服器的背景任務上，HTTP 只是訂閱端：斷線不再中止生成。詳見 web/report_runs.py。

/** 重連一個進行中的 run：先收重播、再接直播。POST 是因為 readSSE 只走 POST（見該檔）。 */
export function streamReportRun(runId: string, signal: AbortSignal): AsyncGenerator<RawSSEEvent> {
  return readSSE(`/api/report-runs/${encodeURIComponent(runId)}/stream`, undefined, signal, 'GET')
}

export const activeReportRunSchema = z.object({
  run_id: z.string(),
  qa_id: z.string().nullish(),
  question: z.string(),
  elapsed_ms: z.number().default(0),
})
export type ActiveReportRun = z.infer<typeof activeReportRunSchema>

/** 某對話目前仍在背景生成的研報。載入對話時據此自動接回進度框。 */
export function getActiveReportRuns(conversationId: string): Promise<ActiveReportRun[]> {
  return getJSON(
    `/api/report-runs?conversation_id=${encodeURIComponent(conversationId)}`,
    z.object({ runs: z.array(activeReportRunSchema) }),
    { cache: 'no-store' },
  ).then((r) => r.runs)
}

/** 主動中止背景生成。關掉分頁不再等於取消，這是唯一的停止手段。 */
export async function cancelReportRun(runId: string): Promise<void> {
  await fetch(`/api/report-runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
    credentials: 'same-origin',
  })
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

// M9b 換皮重出：用既有 markdown 以另一模板產新 rendition（零 LLM）。
// locale 不在參數裡——後端一律沿用產出當時存下的值，換皮只換版型、不改輸出語言。
export async function rerenderReport(
  reportId: string,
  templateId: string,
): Promise<{ rendition_id: string; template_id: string | null }> {
  const resp = await fetch(`/api/report-doc/${encodeURIComponent(reportId)}/rerender`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ template_id: templateId }),
    credentials: 'same-origin',
  })
  if (!resp.ok) throw new Error(`rerender failed: ${resp.status}`)
  return resp.json()
}

export function getReportTemplates(): Promise<ReportTemplate[]> {
  return getJSON('/api/report-templates', z.object({ templates: z.array(reportTemplateSchema) }), { cache: 'no-store' })
    .then((r) => r.templates)
}

export function getConversation(id: string): Promise<ConversationTurn[]> {
  return getJSON(`/api/conversations/${encodeURIComponent(id)}`, z.array(conversationTurnSchema), { cache: 'no-store' })
}

/**
 * 刪整個對話串。**失敗一定要 throw**——這裡先前是靜默的。
 *
 * 舊版只看 404/405 決定要不要走 POST fallback，其餘一律當成功：回應主體從不讀，
 * 500 也直接忽略。後果不只是「列還在」——`ConversationList` 在 `onSuccess` 裡會
 * `navigate('/ask')`，所以刪除失敗時使用者**還會被送離當前對話**，而且沒有任何
 * 錯誤訊息。而後端在「什麼都沒刪到」時回的是 200 `{"ok": false}`，不是錯誤碼。
 *
 * 404/405 → POST fallback 是刻意保留的（某些代理/邊緣對 DELETE 不穩）。但 404 也
 * 可能是「這個對話不存在」，那時 fallback 會再收到一次 `{"ok": false}` 而正確地
 * throw——多打一次請求換到正確的錯誤語意，是值得的。
 */
export async function deleteConversation(id: string): Promise<void> {
  const path = `/api/conversations/${encodeURIComponent(id)}`
  let resp = await fetch(path, { method: 'DELETE', credentials: 'same-origin' })
  if (resp.status === 404 || resp.status === 405) {
    resp = await fetch(`${path}/delete`, { method: 'POST', credentials: 'same-origin' })
  }
  if (!resp.ok) throw new Error(`刪除對話失敗（HTTP ${resp.status}）`)
  // 後端的契約是 200 + {"ok": bool}；ok=false 代表一列都沒刪到（查無此對話串）。
  // 主體解析失敗不當成刪除失敗——DELETE 成功但回應被代理改寫過的情況不該讓
  // 使用者以為東西還在（那會誘導他再按一次）。
  let ok = true
  try {
    const body = (await resp.json()) as { ok?: unknown }
    if (typeof body?.ok === 'boolean') ok = body.ok
  } catch { /* 非 JSON 或空主體：視為成功，理由見上 */ }
  if (!ok) throw new Error('刪除對話失敗：找不到該對話串')
}

/** value 'none' ＝取消評價（後端寫成 NULL）。刻意不是可為 null 的欄位——欄位漏送與
 *  「明確取消」在那種設計下無法區分，客戶端少帶一個欄位就會靜默清掉使用者的評價。 */
export async function sendFeedback(qaId: string, value: 'like' | 'dislike' | 'none'): Promise<void> {
  await fetch('/api/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ qa_id: qaId, value }),
    credentials: 'same-origin',
  })
}
