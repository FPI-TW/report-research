import { ApiError, redirectToLogin } from './api'

export type RawSSEEvent = { event: string; data: unknown }

function parseFrame(frame: string): RawSSEEvent | null {
  let event = 'message'
  let data: string | null = null
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data = (data === null ? '' : data + '\n') + line.slice(5).trim()
  }
  if (data === null) return null
  try {
    return { event, data: JSON.parse(data) }
  } catch {
    return null
  }
}

export async function* readSSE(
  path: string,
  body: unknown,
  signal: AbortSignal,
  method: 'POST' | 'GET' = 'POST'
): AsyncGenerator<RawSSEEvent> {
  // GET 用於「重連既有背景 run」——那條沒有請求體，帶 Content-Type 與空 body 會被
  // Starlette 當成畸形請求。
  const resp = await fetch(path, {
    method,
    ...(method === 'POST'
      ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
      : {}),
    credentials: 'same-origin',
    signal,
  })
  if (resp.status === 401) {
    redirectToLogin()
    throw new ApiError(401, '未登入')
  }
  // 429＝排隊已滿（後端在送出 200 之前擋下，見 web/concurrency.py）。這是唯一一種
  // 「後端有話要對使用者說」的非 200，所以要把 detail 帶出去；其餘照舊只帶狀態碼。
  if (resp.status === 429) {
    let detail = '伺服器忙碌中，請稍後再試'
    try {
      const body = await resp.json()
      if (body && typeof body.detail === 'string') detail = body.detail
    } catch { /* 非 JSON body：用預設文案，不要因此變成看不懂的錯誤 */ }
    throw new ApiError(429, detail)
  }
  if (!resp.ok || !resp.body) throw new ApiError(resp.status, `HTTP ${resp.status}`)
  const reader = resp.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let sep: number
    while ((sep = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, sep)
      buf = buf.slice(sep + 2)
      const ev = parseFrame(frame)
      if (ev) yield ev
    }
  }
}
