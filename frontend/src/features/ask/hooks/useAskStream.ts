import { useCallback, useRef, useState } from 'react'
import { streamAsk } from '../lib/sse'
import { buildAskBody, emptyTurn, historyToTurn, type TurnState } from '../lib/conversation'
import type { HistoryItem } from '../schemas'

const HTTP = /^https?:\/\//i

export interface UseAskStream {
  turns: TurnState[]
  conversationId: string | null
  streaming: boolean
  send: (question: string) => void
  loadConversation: (items: HistoryItem[], id: string) => void
  newConversation: () => void
}

export function useAskStream(): UseAskStream {
  const [turns, setTurns] = useState<TurnState[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [streaming, setStreaming] = useState(false)
  const seqRef = useRef(0)
  const ctrlRef = useRef<AbortController | null>(null)
  const idRef = useRef(0)

  const cancelActive = useCallback(() => {
    seqRef.current += 1 // 讓飛行中串流的 latest-wins 立刻失效
    if (ctrlRef.current) {
      ctrlRef.current.abort()
      ctrlRef.current = null
    }
  }, [])

  const send = useCallback(
    (question: string) => {
      const q = question.trim()
      if (!q) return
      cancelActive()
      const mySeq = seqRef.current
      const turnId = `t${(idRef.current += 1)}`
      setStreaming(true)
      setTurns((prev) => [...prev, emptyTurn(turnId, q)])
      const ctrl = new AbortController()
      ctrlRef.current = ctrl

      const update = (fn: (t: TurnState) => TurnState) => {
        if (mySeq !== seqRef.current) return // latest-wins：陳舊串流不寫 UI
        setTurns((prev) => prev.map((t) => (t.id === turnId ? fn(t) : t)))
      }

      void (async () => {
        let started = false
        let notice = false
        try {
          for await (const evt of streamAsk(buildAskBody(q, conversationId), ctrl.signal)) {
            if (mySeq !== seqRef.current) return
            if (evt.event === 'sources') {
              update((t) => ({ ...t, sources: evt.data }))
            } else if (evt.event === 'status') {
              update((t) => ({
                ...t,
                stage: evt.data.stage,
                webUsed: t.webUsed || evt.data.stage === 'searching_web',
              }))
            } else if (evt.event === 'ext_sources') {
              update((t) => ({ ...t, extSources: evt.data.filter((s) => HTTP.test(s.url)) }))
            } else if (evt.event === 'token') {
              started = true
              update((t) => ({ ...t, answer: t.answer + evt.data, phase: 'streaming' }))
            } else if (evt.event === 'notice') {
              notice = true
              started = true
              update((t) => ({ ...t, phase: 'notice', notice: evt.data }))
            } else if (evt.event === 'done') {
              if (evt.data.conversation_id && mySeq === seqRef.current) {
                setConversationId(evt.data.conversation_id)
              }
              update((t) => ({
                ...t,
                qaId: evt.data.qa_id ?? null,
                thinkingMs: typeof evt.data.thinking_ms === 'number' ? evt.data.thinking_ms : t.thinkingMs,
                offerReport: Boolean(evt.data.offer_report),
                reportTitle: evt.data.report_title ?? null,
                phase: t.phase === 'notice' ? 'notice' : 'done',
              }))
            } else if (evt.event === 'error') {
              update((t) => ({ ...t, phase: 'error', errorMsg: '問答服務發生錯誤，請稍後再試。' }))
              return
            }
          }
          if (mySeq !== seqRef.current) return
          if (!started) {
            update((t) => ({ ...t, phase: 'error', errorMsg: '沒有取得回答，請稍後再試。' }))
          } else if (!notice) {
            update((t) => (t.phase === 'error' ? t : { ...t, phase: 'done' }))
          }
        } catch (e) {
          if (mySeq !== seqRef.current) return
          if (e instanceof DOMException && e.name === 'AbortError') return
          update((t) => ({ ...t, phase: 'error', errorMsg: '查詢逾時或失敗，請稍後再試。' }))
        } finally {
          if (mySeq === seqRef.current) {
            ctrlRef.current = null
            setStreaming(false)
          }
        }
      })()
    },
    [cancelActive, conversationId],
  )

  const loadConversation = useCallback(
    (items: HistoryItem[], id: string) => {
      cancelActive()
      setStreaming(false)
      setConversationId(id)
      // id 用 qa_log 的 it.id（全域唯一）而非位置索引：位置式 h0/h1... 會在不同對話間重複，
      // 導致 key={t.id} 不重掛載（dismissed 洩漏）且 ReportPanel 的 report.turnId===turn.id 誤配到別的對話。
      setTurns(items.map((it) => historyToTurn(it, `h${it.id}`)))
    },
    [cancelActive],
  )

  const newConversation = useCallback(() => {
    cancelActive()
    setStreaming(false)
    setConversationId(null)
    setTurns([])
  }, [cancelActive])

  return { turns, conversationId, streaming, send, loadConversation, newConversation }
}
