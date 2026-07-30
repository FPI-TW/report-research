import { afterEach, expect, test, vi } from 'vitest'
import { readSSE, type RawSSEEvent } from './readSSE'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function sseResponse(chunks: string[], status = 200): Response {
  const enc = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      for (const ch of chunks) c.enqueue(enc.encode(ch))
      c.close()
    },
  })
  return new Response(stream, { status })
}

async function collect(path: string, body: unknown): Promise<RawSSEEvent[]> {
  const out: RawSSEEvent[] = []
  for await (const ev of readSSE(path, body, new AbortController().signal)) out.push(ev)
  return out
}

test('解析多幀、跨 chunk 邊界拼接、丟棄壞幀', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      sseResponse([
        'event: status\ndata: {"stage":"understanding"}\n\n',
        'event: token\ndata: "片', // 半幀（跨 chunk）
        '段"\n\n',
        'event: oops\ndata: {bad json}\n\n', // 壞幀 → 丟棄
        'event: done\ndata: {"conversation_id":"c1"}\n\n',
      ])
    )
  )
  const evs = await collect('/api/ask', { question: 'x' })
  expect(evs).toEqual([
    { event: 'status', data: { stage: 'understanding' } },
    { event: 'token', data: '片段' },
    { event: 'done', data: { conversation_id: 'c1' } },
  ])
})

test('忽略心跳註解幀，不影響事件解析', async () => {
  // web/server.py 的 _with_heartbeat 在長靜默時插 `: keep-alive` 註解幀，避免
  // nginx proxy_read_timeout(60s) 在研報逐節檢索期間切斷連線。前端必須當它不存在。
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      sseResponse([
        'event: status\ndata: {"stage":"retrieving"}\n\n',
        ': keep-alive\n\n',
        ': keep-alive\n\n',
        'event: token\ndata: "內文"\n\n',
        'event: done\ndata: {"ok":true}\n\n',
      ])
    )
  )
  const evs = await collect('/api/report', {})
  expect(evs).toEqual([
    { event: 'status', data: { stage: 'retrieving' } },
    { event: 'token', data: '內文' },
    { event: 'done', data: { ok: true } },
  ])
})

test('壞幀被丟棄時留下痕跡，心跳幀不留', async () => {
  // 「靜默丟棄」在本專案咬過兩次（section_draft 撐了好幾個里程碑沒人發現）。壞 JSON
  // 仍然只是被丟掉——但至少開發時 console 看得到。心跳幀是預期常態，不可一起吐噪音。
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
  vi.stubGlobal('fetch', vi.fn(async () =>
    sseResponse([
      ': keep-alive\n\n',
      'event: oops\ndata: {bad json}\n\n',
      'event: done\ndata: {"ok":true}\n\n',
    ])
  ))
  const evs = await collect('/api/ask', {})
  expect(evs).toEqual([{ event: 'done', data: { ok: true } }])
  expect(warn).toHaveBeenCalledTimes(1)
  expect(warn.mock.calls[0]).toEqual(['[sse] 丟棄事件', 'oops', expect.anything()])
})

test('401 導向登入並拋 ApiError', async () => {
  const assign = vi.fn()
  vi.stubGlobal('location', { pathname: '/ask', search: '', assign })
  vi.stubGlobal('fetch', vi.fn(async () => sseResponse([], 401)))
  await expect(collect('/api/ask', {})).rejects.toMatchObject({ status: 401 })
  expect(assign).toHaveBeenCalledWith(expect.stringContaining('/login?next='))
})

test('429 帶出後端的 detail，而不是只有一個狀態碼', async () => {
  // 排隊已滿是唯一一種「後端有話要對使用者說」的非 200。丟掉 detail 的話，畫面只剩
  // 「查詢逾時或失敗」——一個錯的診斷，會讓人一直重按。
  vi.stubGlobal('fetch', vi.fn(async () =>
    new Response(JSON.stringify({ detail: '問答排隊人數已滿，請稍後再試' }), {
      status: 429, headers: { 'Content-Type': 'application/json' },
    })
  ))
  await expect(collect('/api/ask', {})).rejects.toMatchObject({
    status: 429, message: '問答排隊人數已滿，請稍後再試',
  })
})

test('429 但 body 不是 JSON 時退回可讀文案（不是空字串）', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>nginx</html>', { status: 429 })))
  await expect(collect('/api/ask', {})).rejects.toMatchObject({ status: 429, message: '伺服器忙碌中，請稍後再試' })
})
