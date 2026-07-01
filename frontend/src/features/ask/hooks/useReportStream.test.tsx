import { describe, expect, test, vi, beforeEach, afterEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'

// 可控的 streamReport mock：每次呼叫從佇列取一個 async generator factory。
// 用 vi.hoisted 宣告 genQueue —— vi.mock 會提升到 import 之上，factory 不能引用
// 一般外層 const（會 ReferenceError），須經 hoisted 共享。
const { genQueue } = vi.hoisted(() => ({ genQueue: [] as Array<() => AsyncGenerator<unknown>> }))
vi.mock('../lib/sse', () => ({
  streamReport: () => {
    const make = genQueue.shift()
    if (!make) throw new Error('no gen queued')
    return make()
  },
}))

import { useReportStream } from './useReportStream'
import type { ReportEvent } from '../lib/reportEvents'

function gen(events: ReportEvent[], opts: { hang?: boolean } = {}): () => AsyncGenerator<ReportEvent> {
  return async function* () {
    for (const e of events) yield e
    if (opts.hang) await new Promise(() => {}) // 永不結束（模擬仍在飛行）
  }
}

const body = { question: 'q', conversation_id: null, qa_id: null }

beforeEach(() => { genQueue.length = 0 })
afterEach(() => vi.clearAllMocks())

describe('useReportStream', () => {
  test('start 累積 token 並於 done 完成', async () => {
    genQueue.push(gen([
      { event: 'status', data: { stage: 'retrieving' } },
      { event: 'token', data: '研' },
      { event: 'token', data: '報' },
      { event: 'done', data: { report_id: 'r1', title: 'T', download_url: '/x' } },
    ]))
    const { result } = renderHook(() => useReportStream())
    act(() => result.current.start('t1', body))
    await waitFor(() => expect(result.current.report.phase).toBe('done'))
    expect(result.current.report.markdown).toBe('研報')
    expect(result.current.report.stage).toBe('retrieving')
    expect(result.current.report.done?.report_id).toBe('r1')
  })

  test('latest-wins：新 start 後舊串流的後續事件不寫入', async () => {
    // 第一次 hang（吐一個 token 後不結束）；第二次正常完成
    genQueue.push(gen([{ event: 'token', data: '舊' }], { hang: true }))
    genQueue.push(gen([
      { event: 'token', data: '新' },
      { event: 'done', data: { report_id: 'r2', title: 'T2', download_url: '/y' } },
    ]))
    const { result } = renderHook(() => useReportStream())
    act(() => result.current.start('t1', body))
    await waitFor(() => expect(result.current.report.markdown).toBe('舊'))
    act(() => result.current.start('t2', body))
    await waitFor(() => expect(result.current.report.phase).toBe('done'))
    expect(result.current.report.turnId).toBe('t2')
    expect(result.current.report.markdown).toBe('新')
    expect(result.current.report.done?.report_id).toBe('r2')
  })

  test('error 事件 → phase:error 帶訊息', async () => {
    genQueue.push(gen([{ event: 'error', data: { detail: '生成失敗' } }]))
    const { result } = renderHook(() => useReportStream())
    act(() => result.current.start('t1', body))
    await waitFor(() => expect(result.current.report.phase).toBe('error'))
    expect(result.current.report.error).toBe('生成失敗')
  })

  test('串流結束但無 done/error → phase:error（研報生成未完成）', async () => {
    genQueue.push(gen([{ event: 'status', data: { stage: 'writing' } }]))
    const { result } = renderHook(() => useReportStream())
    act(() => result.current.start('t1', body))
    await waitFor(() => expect(result.current.report.phase).toBe('error'))
    expect(result.current.report.error).toBe('研報生成未完成')
  })

  test('cancel 中止進行中串流且不拋出', async () => {
    genQueue.push(gen([{ event: 'token', data: 'x' }], { hang: true }))
    const { result } = renderHook(() => useReportStream())
    act(() => result.current.start('t1', body))
    await waitFor(() => expect(result.current.report.markdown).toBe('x'))
    expect(() => act(() => result.current.cancel())).not.toThrow()
  })

  test('cancel 將 report 重置為 idle 狀態（不留舊 turnId，避免跨對話誤配）', async () => {
    genQueue.push(gen([{ event: 'token', data: 'x' }], { hang: true }))
    const { result } = renderHook(() => useReportStream())
    act(() => result.current.start('t1', body))
    await waitFor(() => expect(result.current.report.markdown).toBe('x'))
    act(() => result.current.cancel())
    expect(result.current.report).toEqual({
      turnId: null,
      phase: 'idle',
      stage: null,
      markdown: '',
      done: null,
      error: null,
    })
  })

  test('start 內部呼叫 cancel 後立即設回 generating：淨效果不受 cancel 重置影響', async () => {
    genQueue.push(gen([
      { event: 'token', data: '報' },
      { event: 'done', data: { report_id: 'r1', title: 'T', download_url: '/x' } },
    ]))
    const { result } = renderHook(() => useReportStream())
    act(() => result.current.start('t1', body))
    // start() 內部同步呼叫 cancel()（重置為 idle）後緊接 setReport(generating)，
    // React 批次更新使兩者落在同一次 render，外部只會觀察到 generating，不會看到中間的 idle。
    expect(result.current.report.turnId).toBe('t1')
    expect(result.current.report.phase).toBe('generating')
    await waitFor(() => expect(result.current.report.phase).toBe('done'))
  })
})
