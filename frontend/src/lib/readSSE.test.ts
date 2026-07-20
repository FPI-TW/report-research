import { afterEach, expect, test, vi } from 'vitest'
import { readSSE, type RawSSEEvent } from './readSSE'

afterEach(() => vi.unstubAllGlobals())

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

test('401 導向登入並拋 ApiError', async () => {
  const assign = vi.fn()
  vi.stubGlobal('location', { pathname: '/ask', search: '', assign })
  vi.stubGlobal('fetch', vi.fn(async () => sseResponse([], 401)))
  await expect(collect('/api/ask', {})).rejects.toMatchObject({ status: 401 })
  expect(assign).toHaveBeenCalledWith(expect.stringContaining('/login?next='))
})
