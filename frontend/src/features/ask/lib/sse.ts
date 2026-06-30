import type { AskEvent } from './sseEvents'
import { ApiError, redirectToLogin } from '../../../lib/api'

/** SSE frame（event:/data: 行）→ 型別化事件；無 data 或壞 JSON 回 null。 */
export function parseFrame(frame: string): AskEvent | null {
  let event = 'message'
  let data = ''
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data += line.slice(5).trim()
  }
  if (!data) return null
  try {
    return { event, data: JSON.parse(data) } as AskEvent
  } catch {
    return null
  }
}

/**
 * POST /api/ask（text/event-stream），以 fetch reader 手解 SSE，yield 型別化事件。
 * 連線/abort 由呼叫端透過 signal 管理。401 → 導向登入並拋；非 OK/無 body → 拋。
 */
export async function* streamAsk(body: object, signal: AbortSignal): AsyncGenerator<AskEvent> {
  const resp = await fetch('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
    credentials: 'same-origin',
  })
  if (resp.status === 401) {
    redirectToLogin()
    throw new ApiError(401, '未登入')
  }
  if (!resp.ok || !resp.body) throw new Error('bad response')
  const reader = resp.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let idx: number
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const evt = parseFrame(buf.slice(0, idx))
      buf = buf.slice(idx + 2)
      if (evt) yield evt
    }
  }
}
