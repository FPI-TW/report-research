import { z } from 'zod'
import { getJSON } from './api'
import { readSSE, type RawSSEEvent } from './readSSE'
import { conversationTurnSchema, qaVersionSchema, type ConversationTurn, type QaVersion } from './askSchemas'
import type { Locale } from './useLocale'

export function streamAsk(
  body: { question: string; conversation_id?: string; regenerate_of?: string; edit_of?: string; request_id?: string; locale?: Locale; web?: boolean },
  signal: AbortSignal,
): AsyncGenerator<RawSSEEvent> {
  return readSSE('/api/ask', body, signal)
}

export interface StopBody {
  question: string
  conversation_id?: string | null
  partial_answer: string
  sources?: unknown[]
  ext_sources?: unknown[]
  stages?: string[]
  regenerate_of?: string
  edit_of?: string
  request_id?: string
}

// keepalive 請求的本體有 64 KiB 上限（瀏覽器規定，超過就整個送不出去），留一點餘裕。
const KEEPALIVE_MAX_BYTES = 60_000

function byteLength(body: StopBody): number {
  return new TextEncoder().encode(JSON.stringify(body)).length
}

/** 關頁時送的停止請求要塞進 keepalive 的上限：先捨來源，再截短已生成的文字，問題本身一定留著。 */
export function fitKeepalive(body: StopBody): StopBody {
  if (byteLength(body) <= KEEPALIVE_MAX_BYTES) return body
  let slim: StopBody = { ...body, sources: [], ext_sources: [] }
  while (byteLength(slim) > KEEPALIVE_MAX_BYTES && slim.partial_answer) {
    slim = { ...slim, partial_answer: slim.partial_answer.slice(0, Math.floor(slim.partial_answer.length / 2)) }
  }
  return slim
}

export async function stopAsk(body: StopBody, opts: { keepalive?: boolean } = {}): Promise<{ qa_id: string }> {
  // 帶逾時：呼叫端已先把 UI 標成 stopped、事後才補 qaId（fail-open），這個請求
  // 掛住十幾秒只會讓晚到的回應更容易撞上世代檢查被丟棄，不如早點放棄。
  // keepalive：重整或關頁時送出，請求要活過頁面卸載（useAskController 的 pagehide）。
  const resp = await fetch('/api/ask/stop', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts.keepalive ? fitKeepalive(body) : body),
    credentials: 'same-origin',
    signal: AbortSignal.timeout(10_000),
    ...(opts.keepalive ? { keepalive: true } : {}),
  })
  if (!resp.ok) throw new Error(`stop failed: ${resp.status}`)
  return resp.json()
}

export function getQaVersions(rootId: string): Promise<QaVersion[]> {
  return getJSON(`/api/qa/${encodeURIComponent(rootId)}/versions`, z.array(qaVersionSchema), { cache: 'no-store' })
}

// 只取閱讀頁要的那一欄。nullish：舊後端沒回這欄時解析照樣成功，呼叫端再當成「開不了」。
const reportHashSchema = z.object({ file_hash: z.string().nullish() })

/**
 * 問答來源 → 閱讀頁網址要的 `file_hash`。
 *
 * SSE 的 `sources` 只帶 `report_id`（閱讀頁以 file_hash 定址），所以「在閱讀頁開啟」在點下去
 * 那一刻才問 `/api/report/{report_id}/full`。被隱藏的研報在那裡回 404，與閱讀頁本身的語意一致。
 */
export async function getReportHash(reportId: string): Promise<string | null> {
  const r = await getJSON(`/api/report/${encodeURIComponent(reportId)}/full`, reportHashSchema, { cache: 'no-store' })
  return r.file_hash ?? null
}

export function getConversation(id: string): Promise<ConversationTurn[]> {
  return getJSON(`/api/conversations/${encodeURIComponent(id)}`, z.array(conversationTurnSchema), { cache: 'no-store' })
}

/**
 * 刪整個對話串。**失敗一定要 throw**——這裡先前是靜默的。
 *
 * 舊版只看 404/405 決定要不要走 POST fallback，其餘一律當成功：回應主體從不讀，
 * 500 也直接忽略。後果不只是「列還在」——`ConversationList` 在 `onSuccess` 裡會
 * `navigate('/assistant')`，所以刪除失敗時使用者**還會被送離當前對話**，而且沒有任何
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
