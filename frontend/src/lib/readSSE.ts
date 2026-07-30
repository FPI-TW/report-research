import { ApiError, redirectToLogin } from './api'

export type RawSSEEvent = { event: string; data: unknown }

/**
 * 丟棄一個 SSE 事件並在開發模式留下痕跡；回傳值恆為 null。
 *
 * 存在的理由：本專案兩次被「靜默丟棄」咬到（`section_draft` 從 M7 起就在送但前端沒有
 * 對應 case，症狀只是「進度條停在 50% 不動」，撐了好幾個里程碑）。這支只加副作用、
 * 不改任何回傳值，所以接上它的風險近乎零。
 *
 * 用 `import.meta.env.DEV` 閘住是因為生產不需要這些噪音——正常運作下 `token` 分支
 * 每秒會經過同一組判斷數十次，一旦有一個型別漂移就會刷爆 console。
 *
 * 住在這裡（SSE 傳輸層）而非 askSchemas：解析失敗有兩種，「JSON 壞掉」在本檔、
 * 「schema 不合」在 askSchemas，兩邊要吐同一種訊息才看得出是同一類問題。
 */
export function rejectEvent(event: string, why: unknown): null {
  if (import.meta.env.DEV) console.warn('[sse] 丟棄事件', event, why)
  return null
}

function parseFrame(frame: string): RawSSEEvent | null {
  let event = 'message'
  let data: string | null = null
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data = (data === null ? '' : data + '\n') + line.slice(5).trim()
  }
  // 無 data 欄位＝心跳註解行（`: keep-alive`），是預期中的常態，不吐警告。
  if (data === null) return null
  try {
    return { event, data: JSON.parse(data) }
  } catch (err) {
    return rejectEvent(event, err)
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
