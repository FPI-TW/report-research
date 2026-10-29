import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { askReducer, initialAskState, turnFromHistory, type AskAction, type AskState } from './askReducer'
import { parseAskEvent } from './askSchemas'
import { streamAsk, getConversation, sendFeedback, stopAsk, getQaVersions, type StopBody } from './askApi'
import { ApiError } from './api'
import { clearPendingConversation, resolvePendingConversation, startPendingConversation } from './pendingConversation'
import { useLocale } from './useLocale'
import { useWebSearch, useWebSearchPaused } from './useWebSearch'

let seq = 0
const newId = () => `t${Date.now()}_${seq++}`

interface StreamMeta { regenerateOf?: string; editOf?: string; fresh: boolean }

/** 新提問（submit，不是重生、不是編輯）：沒跑完也要落庫的那一種，理由見 abortAsk。 */
function isFreshStream(meta: StreamMeta | null): boolean {
  return meta?.fresh === true
}
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
  const [state, reactDispatch] = useReducer(askReducer, initialAskState)
  // stateRef 在 dispatch 當下就同步推進（reducer 是純函式，與 React 算出的 state 一致）。原本靠 effect
  // 在重繪後才同步：串流最後幾個 token 剛 dispatch 就收尾時，停止／補存送出的半成品會少掉那幾個字。
  const stateRef = useRef<AskState>(initialAskState)
  const dispatch = useCallback((action: AskAction) => {
    stateRef.current = askReducer(stateRef.current, action)
    reactDispatch(action)
  }, [])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const locale = useLocale()  // M10b：輸出語言，注入 /api/ask 請求
  // M11：搜尋網路開關。與 locale 同樣在送出當下取值——切換不影響已在跑的那一輪。
  // 網搜暫停期間一律送 false（理由見 useWebSearchPaused：旗標 ask.web_search 沒開）；hook 照呼叫。
  const paused = useWebSearchPaused()
  const web = useWebSearch() && !paused
  const qc = useQueryClient()
  const convRef = useRef<string | null>(null)
  const reqId = useRef(0)
  const askCtrl = useRef<AbortController | null>(null)
  const streamTurnRef = useRef<string | null>(null)
  const streamRequestIdRef = useRef<string | null>(null)
  // 進行中串流的請求體中繼資料。stop() 要據此把 regenerate_of / edit_of 轉送給
  // /api/ask/stop——重生的停止列才接得回版本鏈、編輯的停止列才會觸發後端截斷。
  // 從 state 反推（舊作法：翻 priorVersions）在編輯路徑上根本無值可推，故直接記。
  const streamBodyRef = useRef<StreamMeta | null>(null)
  // 進行中的串流是否已收到 done（之後還有追問建議要收）：收到了就已經落庫，不必再補存。
  const streamDoneRef = useRef(false)
  const versionRequestId = useRef(0)

  // 新對話在側欄的暫存列（見 pendingConversation.ts）。拿到 conversation_id 就先改連過去，
  // 等清單重抓完（真的那一列已在清單裡）才清掉，中間不會有一拍空掉。沒拿到就直接清，
  // 仍重抓一次：後端有沒有留下停止或失敗的列，以清單為準。
  const settlePending = useCallback((turnId: string, conversationId: string | null) => {
    if (conversationId) resolvePendingConversation(qc, turnId, conversationId)
    else clearPendingConversation(qc, turnId, { unresolvedOnly: true })
    void qc.invalidateQueries({ queryKey: ['conversations'] }).then(() => clearPendingConversation(qc, turnId))
  }, [qc])

  // 送 /api/ask/stop 的內容：畫面上那一輪的現況。要在清掉 stream*Ref 之前取。
  const unfinishedBody = useCallback((turnId: string): StopBody => {
    const t = stateRef.current.turns.find(x => x.id === turnId)
    const requestId = streamRequestIdRef.current
    const streamBody = streamBodyRef.current
    // 重生／編輯途中被停止：regenerate_of 讓停止列接回版本鏈，edit_of 讓後端
    // 執行與畫面一致的截斷。兩者都直接取自進行中串流的請求體（streamBodyRef），
    // 不再從 priorVersions 反推——編輯路徑上 priorVersions 已被清空、推不出來。
    return {
      question: t?.question ?? '',
      conversation_id: convRef.current,
      partial_answer: t?.answer ?? '',
      sources: t?.sources ?? [],
      ext_sources: t?.extSources ?? [],
      stages: t?.stages ?? [],
      ...(requestId ? { request_id: requestId } : {}),
      ...(streamBody?.regenerateOf ? { regenerate_of: streamBody.regenerateOf } : {}),
      ...(streamBody?.editOf ? { edit_of: streamBody.editOf } : {}),
    }
  }, [])

  // 沒跑完的那一輪落庫（/api/ask/stop），對話才會留在側欄「最近」、重整後還在。同一個 request_id
  // 的完成與停止由後端唯一索引收斂成同一列，後端自己已記下的失敗輪不會多出一列。
  // `my` 是這一輪的世代：回應在途期間使用者已重生／送出新題／切換對話（都會 ++reqId），
  // convRef、URL 同步、qaId 這些畫面上的副作用套上去就是把新狀態汙染回舊對話——只做落庫與側欄。
  const keepUnfinished = useCallback(async (turnId: string, body: StopBody, my: number) => {
    let qaId: string | null = null
    try {
      qaId = (await stopAsk(body)).qa_id
      if (my === reqId.current) {
        // 首題尚未收到 done 時，資料庫以 COALESCE(conversation_id, id) 將停止列
        // 視為自己的對話。沿用 qa_id，才能讓後續重生／續問留在同一串。
        if (!convRef.current) {
          convRef.current = qaId
          setConversationId(qaId)
        }
        dispatch({ type: 'set-qa', id: turnId, qaId })
      }
    } catch { /* fail-open：畫面維持原狀，僅這一輪沒存到 */ } finally {
      // 開新對話的那一輪：qa_id 就是這一串的 id。不是時 key 對不上，只剩重抓清單。
      settlePending(turnId, body.conversation_id ? null : qaId)
    }
  }, [settlePending, dispatch])

  // 被取代的串流（開新對話、換到別的對話）若是還沒跑完的新提問，照樣落庫，見 keepUnfinished。
  // 重生與編輯不補存：它們取代的是已有的答案，失敗時畫面退回舊版，補存一個空的停止版反而蓋掉它；
  // 要留下半成品請按停止。
  const abortAsk = useCallback(() => {
    const supersededTurn = streamTurnRef.current
    if (askCtrl.current) {
      const unfinished = supersededTurn && isFreshStream(streamBodyRef.current) && !streamDoneRef.current
        ? unfinishedBody(supersededTurn) : null
      askCtrl.current.abort()
      askCtrl.current = null
      streamTurnRef.current = null
      streamRequestIdRef.current = null
      streamBodyRef.current = null
      // 取代中的串流不會再通過 reqId 檢查而觸發 ask-end；先結束它，避免 UI 永久 busy。
      if (supersededTurn) {
        dispatch({ type: 'ask-end', id: supersededTurn })
        if (unfinished) void keepUnfinished(supersededTurn, unfinished, reqId.current)
        else settlePending(supersededTurn, null)
      }
    }
  }, [settlePending, unfinishedBody, keepUnfinished, dispatch])

  // 重整或關頁：串流連線一斷，後端就不會替這一輪落庫。趁頁面還在時送出（keepalive 讓請求活過卸載）。
  useEffect(() => {
    const onPageHide = () => {
      const turnId = streamTurnRef.current
      if (!turnId || !askCtrl.current || !isFreshStream(streamBodyRef.current) || streamDoneRef.current) return
      void stopAsk(unfinishedBody(turnId), { keepalive: true }).catch(() => { /* 頁面都要關了，沒得補救 */ })
    }
    window.addEventListener('pagehide', onPageHide)
    return () => window.removeEventListener('pagehide', onPageHide)
  }, [unfinishedBody])


  // 共用串流：submit/regenerate/editResubmit 皆走此；fresh＝submit 的新提問。
  const runStream = useCallback((turnId: string, body: { question: string; conversation_id?: string; regenerate_of?: string; edit_of?: string }, fresh = false) => {
    abortAsk()
    const my = ++reqId.current
    const ctrl = new AbortController()
    const requestId = newRequestId()
    askCtrl.current = ctrl
    streamTurnRef.current = turnId
    streamRequestIdRef.current = requestId
    streamBodyRef.current = { regenerateOf: body.regenerate_of, editOf: body.edit_of, fresh }
    streamDoneRef.current = false
    // 沒帶 conversation_id＝這一輪會開出新的一串：送出當下就讓它出現在側欄「最近」，不等跑完。
    const creating = !body.conversation_id
    if (creating) startPendingConversation(qc, turnId, body.question)
    // 收尾（仍是目前這一輪時）：沒收到 done 就結束的新提問（失敗、提早斷線、排隊已滿）照樣落庫，
    // 理由見 abortAsk。內容要在清掉 stream*Ref 之前取；回傳的是畫面標好結束狀態之後才做的事。
    const finish = () => {
      const done = streamDoneRef.current
      const unfinished = !done && isFreshStream(streamBodyRef.current) ? unfinishedBody(turnId) : null
      streamTurnRef.current = null; streamRequestIdRef.current = null; streamBodyRef.current = null
      return () => {
        if (unfinished) void keepUnfinished(turnId, unfinished, my)
        else if (creating && !done) settlePending(turnId, null)
      }
    }
    void (async () => {
      try {
        for await (const raw of streamAsk({ ...body, request_id: requestId, locale, web }, ctrl.signal)) {
          if (my !== reqId.current) return
          const ev = parseAskEvent(raw)
          if (!ev) continue
          if (ev.event === 'done') {
            streamDoneRef.current = true
            if (!convRef.current) {
              convRef.current = ev.data.conversation_id
              setConversationId(ev.data.conversation_id)
            }
            if (creating) settlePending(turnId, ev.data.conversation_id)
            else void qc.invalidateQueries({ queryKey: ['conversations'] })
          }
          if (ev.event === 'followups') dispatch({ type: 'followups', id: turnId, data: ev.data })
          else dispatch({ type: 'ask-event', id: turnId, event: ev })
        }
        if (my === reqId.current) {
          const after = finish()
          dispatch({ type: 'ask-end', id: turnId })
          after()
        }
      } catch (err) {
        if (my === reqId.current) {
          const after = finish()
          // 429＝排隊已滿，後端連 SSE 都沒開。這時 'ask-end' 的「查詢逾時或失敗」是
          // 錯的診斷，會讓人一直重按；改用後端給的原因，使用者才知道要等一下。
          if (err instanceof ApiError && err.status === 429) {
            dispatch({ type: 'ask-event', id: turnId, event: { event: 'error', data: { detail: err.message } } })
          } else {
            dispatch({ type: 'ask-end', id: turnId })
          }
          after()
        }
      }
    })()
  }, [abortAsk, settlePending, unfinishedBody, keepUnfinished, qc, locale, web, dispatch])

  const submit = useCallback((question: string) => {
    const q = question.trim()
    if (!q) return
    const id = newId()
    dispatch({ type: 'submit', id, question: q, startedAt: Date.now() })
    const body = convRef.current ? { question: q, conversation_id: convRef.current } : { question: q }
    runStream(id, body, true)
  }, [runStream, dispatch])

  const stop = useCallback(async () => {
    const turnId = streamTurnRef.current
    const body = turnId ? unfinishedBody(turnId) : null
    const my = ++reqId.current
    askCtrl.current?.abort(); askCtrl.current = null
    streamTurnRef.current = null
    streamRequestIdRef.current = null
    streamBodyRef.current = null
    if (!turnId || !body) return
    const t = stateRef.current.turns.find(x => x.id === turnId)
    // 先落地 stopped，再等 /api/ask/stop 的回應補 qaId：token 已停，畫面不能
    // 繼續掛在「生成中」等一個沒有逾時保證的網路往返——期間停止鈕看起來像壞掉。
    dispatch({ type: 'ask-stop', id: turnId, qaId: t?.qaId ?? null })
    await keepUnfinished(turnId, body, my)
  }, [unfinishedBody, keepUnfinished, dispatch])

  const regenerate = useCallback((turnId: string, qaId: string | null, question: string) => {
    dispatch({ type: 'regenerate-start', id: turnId })
    const body: { question: string; conversation_id?: string; regenerate_of?: string } = { question }
    if (convRef.current) body.conversation_id = convRef.current
    if (qaId) body.regenerate_of = qaId
    runStream(turnId, body)
  }, [runStream, dispatch])

  const editResubmit = useCallback((turnId: string, qaId: string | null, newQuestion: string) => {
    const q = newQuestion.trim()
    if (!q) return
    dispatch({ type: 'truncate-after', id: turnId })
    dispatch({ type: 'submit-edit', id: turnId, question: q })
    const body: { question: string; conversation_id?: string; edit_of?: string } = { question: q }
    if (convRef.current) body.conversation_id = convRef.current
    if (qaId) body.edit_of = qaId
    runStream(turnId, body)
  }, [runStream, dispatch])

  const setVersion = useCallback((turnId: string, index: number) => dispatch({ type: 'set-version', id: turnId, index }), [dispatch])

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
  }, [dispatch])

  const loadConversation = useCallback(async (id: string) => {
    abortAsk()
    const my = ++reqId.current
    convRef.current = id
    setConversationId(id)
    try {
      const items = await getConversation(id)
      if (my !== reqId.current) return
      dispatch({ type: 'load', turns: items.map(turnFromHistory) })
    } catch (err) {
      // 404＝這串不存在或不是你的（後端兩者回同一個 404，看不出差別）。若還掛著這個
      // conversation_id，下一題送出時 /api/ask 也會回 404、畫面卡死——改當新對話。
      if (my === reqId.current && err instanceof ApiError && err.status === 404) {
        convRef.current = null
        setConversationId(null)
        dispatch({ type: 'reset' })
      }
      /* 其他失敗不破壞現況 */
    }
  }, [abortAsk, dispatch])

  const newConversation = useCallback(() => {
    abortAsk()
    ++reqId.current
    convRef.current = null
    setConversationId(null)
    dispatch({ type: 'reset' })
  }, [abortAsk, dispatch])

  const setFeedback = useCallback((turnId: string, qaId: string, value: 'like' | 'dislike' | null) => {
    dispatch({ type: 'feedback', id: turnId, value })
    void sendFeedback(qaId, value ?? 'none').catch(() => { /* 回饋失敗不打擾 */ })
  }, [dispatch])

  return {
    state, conversationId, submit, stop, regenerate, editResubmit, setVersion, loadVersions,
    loadConversation, newConversation, setFeedback,
  }
}
