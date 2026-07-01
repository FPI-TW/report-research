import { useCallback, useEffect, useRef, useState } from 'react'
import { streamReport } from '../lib/sse'
import type { ReportStage, ReportDone } from '../lib/reportEvents'

export interface ReportState {
  turnId: string | null
  phase: 'idle' | 'generating' | 'done' | 'error'
  stage: ReportStage | null
  markdown: string
  done: ReportDone | null
  error: string | null
}

export interface UseReportStream {
  report: ReportState // 單一進行中/最近一份
  start: (turnId: string, body: { question: string; conversation_id: string | null; qa_id: string | null }) => void
  cancel: () => void
}

const IDLE_STATE: ReportState = {
  turnId: null,
  phase: 'idle',
  stage: null,
  markdown: '',
  done: null,
  error: null,
}

export function useReportStream(): UseReportStream {
  const [report, setReport] = useState<ReportState>(IDLE_STATE)
  const seqRef = useRef(0)
  const ctrlRef = useRef<AbortController | null>(null)

  const cancel = useCallback(() => {
    seqRef.current += 1 // 讓飛行中串流的 latest-wins 立刻失效
    if (ctrlRef.current) {
      ctrlRef.current.abort()
      ctrlRef.current = null
    }
    setReport(IDLE_STATE) // 重置畫面狀態，避免舊 turnId 殘留跨對話誤配（ReportPanel 的 turnId 比對）
  }, [])

  const start = useCallback(
    (turnId: string, body: { question: string; conversation_id: string | null; qa_id: string | null }) => {
      cancel()
      const mySeq = seqRef.current
      setReport({ turnId, phase: 'generating', stage: null, markdown: '', done: null, error: null })
      const ctrl = new AbortController()
      ctrlRef.current = ctrl

      const update = (fn: (r: ReportState) => ReportState) => {
        if (mySeq !== seqRef.current) return // latest-wins：陳舊串流不寫 UI
        setReport((prev) => fn(prev))
      }

      void (async () => {
        try {
          for await (const evt of streamReport(body, ctrl.signal)) {
            if (mySeq !== seqRef.current) return
            if (evt.event === 'status') {
              update((r) => ({ ...r, stage: evt.data.stage }))
            } else if (evt.event === 'token') {
              update((r) => ({ ...r, markdown: r.markdown + evt.data }))
            } else if (evt.event === 'done') {
              update((r) => ({ ...r, phase: 'done', done: evt.data }))
            } else if (evt.event === 'error') {
              update((r) => ({ ...r, phase: 'error', error: evt.data.detail ?? '研報生成失敗' }))
              return
            }
            // sources：研報串流不使用來源清單事件，忽略
          }
          if (mySeq !== seqRef.current) return
          update((r) => (r.phase === 'done' ? r : { ...r, phase: 'error', error: '研報生成未完成' }))
        } catch (e) {
          if (mySeq !== seqRef.current) return
          if (e instanceof DOMException && e.name === 'AbortError') return
          update((r) => ({ ...r, phase: 'error', error: '研報生成失敗' }))
        } finally {
          if (mySeq === seqRef.current) {
            ctrlRef.current = null
          }
        }
      })()
    },
    [cancel],
  )

  // 卸載時中止飛行中的研報串流（例如切頁離開 AskPage），避免請求繼續跑到完成、
  // setState 淪為無效的 no-op（元件已不存在）。不遞增 seqRef：元件本身已卸載，
  // 不需要讓後續更新失效，單純釋放底層連線即可。
  useEffect(() => () => ctrlRef.current?.abort(), [])

  return { report, start, cancel }
}
