import { expect, test, afterEach, vi } from 'vitest'
import { streamReport } from './sse'

function sseResponse(frames: string[]) {
  const body = frames.join('')
  const enc = new TextEncoder()
  return {
    status: 200,
    ok: true,
    body: {
      getReader() {
        let sent = false
        return {
          read: async () =>
            sent ? { done: true, value: undefined } : ((sent = true), { done: false, value: enc.encode(body) }),
        }
      },
    },
  } as unknown as Response
}

afterEach(() => vi.restoreAllMocks())

test('streamReport 解析 status→token→done 事件序', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    sseResponse([
      'event: status\ndata: {"stage": "writing"}\n\n',
      'event: token\ndata: "## 標題\\n"\n\n',
      'event: done\ndata: {"report_id":"r1","title":"T","download_url":"/api/report-doc/r1/pdf"}\n\n',
    ]),
  )
  const evts: unknown[] = []
  for await (const e of streamReport({ question: 'q' }, new AbortController().signal)) evts.push(e)
  expect(evts).toEqual([
    { event: 'status', data: { stage: 'writing' } },
    { event: 'token', data: '## 標題\n' },
    { event: 'done', data: { report_id: 'r1', title: 'T', download_url: '/api/report-doc/r1/pdf' } },
  ])
})
