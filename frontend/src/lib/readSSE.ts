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
  signal: AbortSignal
): AsyncGenerator<RawSSEEvent> {
  const resp = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    credentials: 'same-origin',
    signal,
  })
  if (resp.status === 401) {
    redirectToLogin()
    throw new ApiError(401, '未登入')
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
