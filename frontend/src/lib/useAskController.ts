import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { askReducer, initialAskState, turnFromHistory, type AskState } from './askReducer'
import { parseAskEvent } from './askSchemas'
import { streamAsk, getConversation, sendFeedback, stopAsk, getQaVersions } from './askApi'
import { ApiError } from './api'
import { useLocale } from './useLocale'
import { useWebSearch, WEB_SEARCH_PAUSED } from './useWebSearch'

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
  loadConversation: (id: string) => Promise<void>
  newConversation: () => void
  /** value null ＝取消評價（再點一次已亮起的那顆）；送到後端會轉成 'none'。 */
  setFeedback: (turnId: string, qaId: string, value: 'like' | 'dislike' | null) => void
}

export function useAskController(): UseAskController {
  const [state, dispatch] = useReducer(askReducer, initialAskState)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const locale = useLocale()  // M10b：輸出語言，注入 /api/ask 請求
  // M11：搜尋網路開關。與 locale 同樣在送出當下取值——切換不影響已在跑的那一輪。
  // 網搜暫停期間一律送 false（理由見 WEB_SEARCH_PAUSED）；hook 照呼叫，恢復時只改常數。
  const web = useWebSearch() && !WEB_SEARCH_PAUSED
  const qc = useQueryClient()
  const convRef = useRef<string | null>(null)
  const reqId = useRef(0)
  const askCtrl = useRef<AbortController | null>(null)
  const streamTurnRef = useRef<string | null>(null)
  const streamRequestIdRef = useRef<string | null>(null)
  // 進行中串流的請求體中繼資料。stop() 要據此把 regenerate_of / edit_of 轉送給
  // /api/ask/stop——重生的停止列才接得回版本鏈、編輯的停止列才會觸發後端截斷。
  // 從 state 反推（舊作法：翻 priorVersions）在編輯路徑上根本無值可推，故直接記。
  const streamBodyRef = useRef<{ regenerateOf?: string; editOf?: string } | null>(null)
  const versionRequestId = useRef(0)
  const stateRef = useRef(state)
  useEffect(() => {
    stateRef.current = state
  }, [state])

  const abortAsk = useCallback(() => {
    const supersededTurn = streamTurnRef.current
    if (askCtrl.current) {
      askCtrl.current.abort()
      askCtrl.current = null
      // 取代中的串流不會再通過 reqId 檢查而觸發 ask-end；先結束它，避免 UI 永久 busy。
      if (supersededTurn) dispatch({ type: 'ask-end', id: supersededTurn })
      streamTurnRef.current = null
      streamRequestIdRef.current = null
      streamBodyRef.current = null
    }
  }, [])


  // 共用串流：submit/regenerate/editResubmit 皆走此
  const runStream = useCallback((turnId: string, body: { question: string; conversation_id?: string; regenerate_of?: string; edit_of?: string }) => {
    abortAsk()
    const my = ++reqId.current
    const ctrl = new AbortController()
    const requestId = newRequestId()
    askCtrl.current = ctrl
    streamTurnRef.current = turnId
    streamRequestIdRef.current = requestId
    streamBodyRef.current = { regenerateOf: body.regenerate_of, editOf: body.edit_of }
    void (async () => {
      try {
        for await (const raw of streamAsk({ ...body, request_id: requestId, locale, web }, ctrl.signal)) {
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
        if (my === reqId.current) { streamTurnRef.current = null; streamRequestIdRef.current = null; streamBodyRef.current = null; dispatch({ type: 'ask-end', id: turnId }) }
      } catch (err) {
        if (my === reqId.current) {
          streamTurnRef.current = null; streamRequestIdRef.current = null; streamBodyRef.current = null
          // 429＝排隊已滿，後端連 SSE 都沒開。這時 'ask-end' 的「查詢逾時或失敗」是
          // 錯的診斷，會讓人一直重按；改用後端給的原因，使用者才知道要等一下。
          if (err instanceof ApiError && err.status === 429) {
            dispatch({ type: 'ask-event', id: turnId, event: { event: 'error', data: { detail: err.message } } })
          } else {
            dispatch({ type: 'ask-end', id: turnId })
          }
        }
      }
    })()
  }, [abortAsk, qc, locale, web])

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
    const streamBody = streamBodyRef.current
    const my = ++reqId.current
    askCtrl.current?.abort(); askCtrl.current = null
    streamTurnRef.current = null
    streamRequestIdRef.current = null
    streamBodyRef.current = null
    if (!turnId) return
    const t = stateRef.current.turns.find(x => x.id === turnId)
    let qaId: string | null = t?.qaId ?? null
    // 先落地 stopped，再等 /api/ask/stop 的回應補 qaId：token 已停，畫面不能
    // 繼續掛在「生成中」等一個沒有逾時保證的網路往返——期間停止鈕看起來像壞掉。
    dispatch({ type: 'ask-stop', id: turnId, qaId })
    // 重生／編輯途中被停止：regenerate_of 讓停止列接回版本鏈，edit_of 讓後端
    // 執行與畫面一致的截斷。兩者都直接取自進行中串流的請求體（streamBodyRef），
    // 不再從 priorVersions 反推——編輯路徑上 priorVersions 已被清空、推不出來。
    try {
      const r = await stopAsk({
        question: t?.question ?? '',
        conversation_id: convRef.current,
        partial_answer: t?.answer ?? '',
        sources: t?.sources ?? [],
        ext_sources: t?.extSources ?? [],
        stages: t?.stages ?? [],
        ...(requestId ? { request_id: requestId } : {}),
        ...(streamBody?.regenerateOf ? { regenerate_of: streamBody.regenerateOf } : {}),
        ...(streamBody?.editOf ? { edit_of: streamBody.editOf } : {}),
      })
      // 世代檢查：回應在途期間使用者已重生／送出新題／切換對話（都會 ++reqId），
      // 這裡的副作用（convRef、URL 同步、qaId 覆寫）套上去就是把新狀態汙染回舊
      // 對話——晚到的回應一律丟棄。
      if (my !== reqId.current) return
      qaId = r.qa_id
      // 首題尚未收到 done 時，資料庫以 COALESCE(conversation_id, id) 將停止列
      // 視為自己的對話。沿用 qa_id，才能讓後續重生／續問留在同一串。
      if (!convRef.current) {
        convRef.current = r.qa_id
        setConversationId(r.qa_id)
        void qc.invalidateQueries({ queryKey: ['conversations'] })
      }
      dispatch({ type: 'ask-stop', id: turnId, qaId })
    } catch { /* fail-open：已先標 stopped，僅 qaId 補不上 */ }
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

  const loadConversation = useCallback(async (id: string) => {
    abortAsk()
    const my = ++reqId.current
    convRef.current = id
    setConversationId(id)
    try {
      const items = await getConversation(id)
      if (my !== reqId.current) return
      dispatch({ type: 'load', turns: items.map(turnFromHistory) })
    } catch { /* 載入失敗不破壞現況 */ }
  }, [abortAsk])

  const newConversation = useCallback(() => {
    abortAsk()
    ++reqId.current
    convRef.current = null
    setConversationId(null)
    dispatch({ type: 'reset' })
  }, [abortAsk])

  const setFeedback = useCallback((turnId: string, qaId: string, value: 'like' | 'dislike' | null) => {
    dispatch({ type: 'feedback', id: turnId, value })
    void sendFeedback(qaId, value ?? 'none').catch(() => { /* 回饋失敗不打擾 */ })
  }, [])

  return {
    state, conversationId, submit, stop, regenerate, editResubmit, setVersion, loadVersions,
    loadConversation, newConversation, setFeedback,
  }
}
