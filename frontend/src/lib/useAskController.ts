import { useCallback, useReducer, useRef, useState } from 'react'
import { askReducer, initialAskState, turnFromHistory, type AskState } from './askReducer'
import { parseAskEvent, parseReportEvent } from './askSchemas'
import { streamAsk, streamReport, getConversation, sendFeedback } from './askApi'

let seq = 0
const newId = () => `t${Date.now()}_${seq++}`

export interface UseAskController {
  state: AskState
  conversationId: string | null
  submit: (question: string) => void
  generateReport: (turnId: string, question: string, qaId: string | null) => void
  declineReport: (turnId: string) => void
  loadConversation: (id: string) => Promise<void>
  newConversation: () => void
  setFeedback: (turnId: string, qaId: string, value: 'like' | 'dislike') => void
}

export function useAskController(): UseAskController {
  const [state, dispatch] = useReducer(askReducer, initialAskState)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const convRef = useRef<string | null>(null)
  const reqId = useRef(0)
  const askCtrl = useRef<AbortController | null>(null)
  const reportCtrl = useRef<AbortController | null>(null)
  const reportReqId = useRef(0)
  const reportTurnRef = useRef<string | null>(null)

  const abortAll = useCallback(() => {
    askCtrl.current?.abort(); askCtrl.current = null
    if (reportCtrl.current) {
      reportCtrl.current.abort(); reportCtrl.current = null
      reportReqId.current++
      if (reportTurnRef.current) { dispatch({ type: 'report-cancel', id: reportTurnRef.current }); reportTurnRef.current = null }
    }
  }, [])

  const submit = useCallback((question: string) => {
    const q = question.trim()
    if (!q) return
    abortAll()
    const my = ++reqId.current
    const id = newId()
    const ctrl = new AbortController()
    askCtrl.current = ctrl
    dispatch({ type: 'submit', id, question: q, startedAt: Date.now() })
    void (async () => {
      try {
        const body = convRef.current ? { question: q, conversation_id: convRef.current } : { question: q }
        for await (const raw of streamAsk(body, ctrl.signal)) {
          if (my !== reqId.current) return
          const ev = parseAskEvent(raw)
          if (!ev) continue
          if (ev.event === 'done' && !convRef.current) { convRef.current = ev.data.conversation_id; setConversationId(ev.data.conversation_id) }
          dispatch({ type: 'ask-event', id, event: ev })
        }
        if (my === reqId.current) dispatch({ type: 'ask-end', id })
      } catch {
        if (my === reqId.current) dispatch({ type: 'ask-end', id })
      }
    })()
  }, [abortAll])

  const generateReport = useCallback((turnId: string, question: string, qaId: string | null) => {
    if (reportTurnRef.current && reportTurnRef.current !== turnId) dispatch({ type: 'report-cancel', id: reportTurnRef.current })
    reportCtrl.current?.abort()
    const myReport = ++reportReqId.current
    reportTurnRef.current = turnId
    const ctrl = new AbortController()
    reportCtrl.current = ctrl
    dispatch({ type: 'report-start', id: turnId })
    void (async () => {
      let sawTerminal = false
      try {
        const body: { question: string; conversation_id?: string; qa_id?: string } = { question }
        if (convRef.current) body.conversation_id = convRef.current
        if (qaId) body.qa_id = qaId
        for await (const raw of streamReport(body, ctrl.signal)) {
          if (myReport !== reportReqId.current) return
          const ev = parseReportEvent(raw)
          if (!ev) continue
          if (ev.event === 'done' || ev.event === 'error') sawTerminal = true
          dispatch({ type: 'report-event', id: turnId, event: ev })
        }
        if (myReport === reportReqId.current) {
          reportTurnRef.current = null
          if (!sawTerminal) dispatch({ type: 'report-fail', id: turnId, errorText: '研報生成未完成' })
        }
      } catch {
        if (myReport === reportReqId.current) {
          reportTurnRef.current = null
          if (!ctrl.signal.aborted && !sawTerminal) dispatch({ type: 'report-fail', id: turnId, errorText: '研報生成失敗，請重試' })
        }
      }
    })()
  }, [])

  const declineReport = useCallback((turnId: string) => dispatch({ type: 'report-decline', id: turnId }), [])

  const loadConversation = useCallback(async (id: string) => {
    abortAll()
    const my = ++reqId.current
    convRef.current = id
    setConversationId(id)
    try {
      const items = await getConversation(id)
      if (my === reqId.current) dispatch({ type: 'load', turns: items.map(turnFromHistory) })
    } catch { /* 載入失敗不破壞現況 */ }
  }, [abortAll])

  const newConversation = useCallback(() => {
    abortAll()
    ++reqId.current
    convRef.current = null
    setConversationId(null)
    dispatch({ type: 'reset' })
  }, [abortAll])

  const setFeedback = useCallback((turnId: string, qaId: string, value: 'like' | 'dislike') => {
    dispatch({ type: 'feedback', id: turnId, value })
    void sendFeedback(qaId, value).catch(() => { /* 回饋失敗不打擾 */ })
  }, [])

  return { state, conversationId, submit, generateReport, declineReport, loadConversation, newConversation, setFeedback }
}
