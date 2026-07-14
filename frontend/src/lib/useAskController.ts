import { useCallback, useReducer, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { askReducer, initialAskState, turnFromHistory, type AskState } from './askReducer'
import { parseAskEvent, parseReportEvent } from './askSchemas'
import { streamAsk, streamReport, getConversation, sendFeedback, stopAsk, getQaVersions } from './askApi'

let seq = 0
const newId = () => `t${Date.now()}_${seq++}`
const newRequestId = () => globalThis.crypto?.randomUUID?.()
  ?? `00000000-0000-4000-8000-${`${Date.now().toString(16)}${seq++}`.padStart(12, '0').slice(-12)}`

export interface UseAskController {
  state: AskState
  conversationId: string | null
  submit: (question: string) => void
  stop: () => Promise<void>
  regenerate: (turnId: string, qaId: string | null, question: string) => void
  editResubmit: (turnId: string, qaId: string | null, newQuestion: string) => void
  setVersion: (turnId: string, index: number) => void
  loadVersions: (turnId: string, rootId: string) => Promise<boolean>
  generateReport: (turnId: string, question: string, qaId: string | null) => void
  declineReport: (turnId: string) => void
  loadConversation: (id: string) => Promise<void>
  newConversation: () => void
  setFeedback: (turnId: string, qaId: string, value: 'like' | 'dislike') => void
}

export function useAskController(): UseAskController {
  const [state, dispatch] = useReducer(askReducer, initialAskState)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const qc = useQueryClient()
  const convRef = useRef<string | null>(null)
  const reqId = useRef(0)
  const askCtrl = useRef<AbortController | null>(null)
  const reportCtrl = useRef<AbortController | null>(null)
  const reportReqId = useRef(0)
  const reportTurnRef = useRef<string | null>(null)
  const streamTurnRef = useRef<string | null>(null)
  const streamRequestIdRef = useRef<string | null>(null)
  const versionRequestId = useRef(0)
  const stateRef = useRef(state)
  stateRef.current = state

  const abortAll = useCallback(() => {
    const supersededTurn = streamTurnRef.current
    if (askCtrl.current) {
      askCtrl.current.abort()
      askCtrl.current = null
      // 取代中的串流不會再通過 reqId 檢查而觸發 ask-end；先結束它，避免 UI 永久 busy。
      if (supersededTurn) dispatch({ type: 'ask-end', id: supersededTurn })
      streamTurnRef.current = null
      streamRequestIdRef.current = null
    }
    if (reportCtrl.current) {
      reportCtrl.current.abort(); reportCtrl.current = null
      reportReqId.current++
      if (reportTurnRef.current) { dispatch({ type: 'report-cancel', id: reportTurnRef.current }); reportTurnRef.current = null }
    }
  }, [])

  // 共用串流：submit/regenerate/editResubmit 皆走此
  const runStream = useCallback((turnId: string, body: { question: string; conversation_id?: string; regenerate_of?: string; edit_of?: string }) => {
    abortAll()
    const my = ++reqId.current
    const ctrl = new AbortController()
    const requestId = newRequestId()
    askCtrl.current = ctrl
    streamTurnRef.current = turnId
    streamRequestIdRef.current = requestId
    void (async () => {
      try {
        for await (const raw of streamAsk({ ...body, request_id: requestId }, ctrl.signal)) {
          if (my !== reqId.current) return
          const ev = parseAskEvent(raw)
          if (!ev) continue
          if (ev.event === 'done') {
            if (!convRef.current) {
              convRef.current = ev.data.conversation_id
              setConversationId(ev.data.conversation_id)
            }
            void qc.invalidateQueries({ queryKey: ['conversations'] })
          }
          if (ev.event === 'followups') dispatch({ type: 'followups', id: turnId, data: ev.data })
          else dispatch({ type: 'ask-event', id: turnId, event: ev })
        }
        if (my === reqId.current) { streamTurnRef.current = null; streamRequestIdRef.current = null; dispatch({ type: 'ask-end', id: turnId }) }
      } catch {
        if (my === reqId.current) { streamTurnRef.current = null; streamRequestIdRef.current = null; dispatch({ type: 'ask-end', id: turnId }) }
      }
    })()
  }, [abortAll, qc])

  const submit = useCallback((question: string) => {
    const q = question.trim()
    if (!q) return
    const id = newId()
    dispatch({ type: 'submit', id, question: q, startedAt: Date.now() })
    const body = convRef.current ? { question: q, conversation_id: convRef.current } : { question: q }
    runStream(id, body)
  }, [runStream])

  const stop = useCallback(async () => {
    const turnId = streamTurnRef.current
    const requestId = streamRequestIdRef.current
    ++reqId.current
    askCtrl.current?.abort(); askCtrl.current = null
    streamTurnRef.current = null
    streamRequestIdRef.current = null
    if (!turnId) return
    const t = stateRef.current.turns.find(x => x.id === turnId)
    let qaId: string | null = t?.qaId ?? null
    // 重生途中被停止：priorVersions 已由 regenerate-start 快照被取代的版本，
    // regenerate_of 應為該版本的 qaId 以接回版本鏈（編輯途中 priorVersions 已被
    // submit-edit 清空，regenOf 自然 undefined，不 chain）。
    const regenOf = t && t.priorVersions.length > 0
      ? (t.priorVersions[t.priorVersions.length - 1].qaId ?? undefined)
      : undefined
    try {
      const r = await stopAsk({
        question: t?.question ?? '',
        conversation_id: convRef.current,
        partial_answer: t?.answer ?? '',
        sources: t?.sources ?? [],
        ext_sources: t?.extSources ?? [],
        stages: t?.stages ?? [],
        ...(requestId ? { request_id: requestId } : {}),
        ...(regenOf ? { regenerate_of: regenOf } : {}),
      })
      qaId = r.qa_id
      // 首題尚未收到 done 時，資料庫以 COALESCE(conversation_id, id) 將停止列
      // 視為自己的對話。沿用 qa_id，才能讓後續重生／續問留在同一串。
      if (!convRef.current) {
        convRef.current = r.qa_id
        setConversationId(r.qa_id)
        void qc.invalidateQueries({ queryKey: ['conversations'] })
      }
    } catch { /* fail-open：仍標 stopped */ }
    dispatch({ type: 'ask-stop', id: turnId, qaId })
  }, [qc])

  const regenerate = useCallback((turnId: string, qaId: string | null, question: string) => {
    dispatch({ type: 'regenerate-start', id: turnId })
    const body: { question: string; conversation_id?: string; regenerate_of?: string } = { question }
    if (convRef.current) body.conversation_id = convRef.current
    if (qaId) body.regenerate_of = qaId
    runStream(turnId, body)
  }, [runStream])

  const editResubmit = useCallback((turnId: string, qaId: string | null, newQuestion: string) => {
    const q = newQuestion.trim()
    if (!q) return
    dispatch({ type: 'truncate-after', id: turnId })
    dispatch({ type: 'submit-edit', id: turnId, question: q })
    const body: { question: string; conversation_id?: string; edit_of?: string } = { question: q }
    if (convRef.current) body.conversation_id = convRef.current
    if (qaId) body.edit_of = qaId
    runStream(turnId, body)
  }, [runStream])

  const setVersion = useCallback((turnId: string, index: number) => dispatch({ type: 'set-version', id: turnId, index }), [])

  const loadVersions = useCallback(async (turnId: string, rootId: string) => {
    const requestId = ++versionRequestId.current
    try {
      const versions = await getQaVersions(rootId)
      const turn = stateRef.current.turns.find(item => item.id === turnId)
      // 避免晚到的舊請求覆蓋目前已切換到其他題目的版本資料；空結果也不應把
      // 已知的 versionCount 歸零，否則 pager 會顯示 1/0。
      if (requestId !== versionRequestId.current || !turn || turn.rootQaId !== rootId || versions.length === 0) return false
      dispatch({ type: 'load-versions', id: turnId, versions })
      return true
    } catch { return false }
  }, [])

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

  return {
    state, conversationId, submit, stop, regenerate, editResubmit, setVersion, loadVersions,
    generateReport, declineReport, loadConversation, newConversation, setFeedback,
  }
}
