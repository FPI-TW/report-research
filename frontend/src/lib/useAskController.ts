import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { askReducer, initialAskState, turnFromHistory, type AskState, type Turn } from './askReducer'
import { parseAskEvent, parseReportEvent } from './askSchemas'
import {
  streamAsk, streamReport, streamReportRun, getActiveReportRuns, cancelReportRun,
  getConversation, sendFeedback, stopAsk, getQaVersions, setReportOffer,
} from './askApi'
import type { RawSSEEvent } from './readSSE'
import { ApiError } from './api'
import { useLocale } from './useLocale'

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
  generateReport: (turnId: string, question: string, qaId: string | null, templateId?: string) => void
  /** 「暫時不用」＝收合成小入口（跨重整持久），不是刪除；restore 還原成邀請卡。 */
  declineReport: (turnId: string, qaId: string | null) => void
  restoreReportOffer: (turnId: string, qaId: string | null) => void
  cancelReport: (turnId: string, runId: string) => void
  loadConversation: (id: string) => Promise<void>
  newConversation: () => void
  /** value null ＝取消評價（再點一次已亮起的那顆）；送到後端會轉成 'none'。 */
  setFeedback: (turnId: string, qaId: string, value: 'like' | 'dislike' | null) => void
}

export function useAskController(): UseAskController {
  const [state, dispatch] = useReducer(askReducer, initialAskState)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const locale = useLocale()  // M10b：輸出語言，注入 /api/ask 與 /api/report 請求
  const qc = useQueryClient()
  const convRef = useRef<string | null>(null)
  const reqId = useRef(0)
  const askCtrl = useRef<AbortController | null>(null)
  const reportCtrl = useRef<AbortController | null>(null)
  const reportReqId = useRef(0)
  const reportTurnRef = useRef<string | null>(null)
  // 目前訂閱的背景 run。用途有二：斷線後自動接回，以及「取消生成」要送給誰。
  const reportRunRef = useRef<string | null>(null)
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

  /**
   * 放掉研報訂閱，但**不動伺服器上的生成**。
   *
   * 研報改為背景執行後，「離開」與「取消」是兩件事：切換對話、送出新問題都只是不再看，
   * 生成照跑，回來時 loadConversation 會自動接回。舊實作在這裡 dispatch report-cancel，
   * 把面板打回「未生成」——那在當時是真的（斷線即中止），現在會變成謊報。
   */
  const detachReport = useCallback(() => {
    if (!reportCtrl.current) return
    reportCtrl.current.abort()
    reportCtrl.current = null
    reportReqId.current++
    reportTurnRef.current = null
    reportRunRef.current = null
  }, [])

  const abortAll = useCallback(() => {
    abortAsk()
    detachReport()
  }, [abortAsk, detachReport])

  // 共用串流：submit/regenerate/editResubmit 皆走此
  const runStream = useCallback((turnId: string, body: { question: string; conversation_id?: string; regenerate_of?: string; edit_of?: string }) => {
    // 刻意只中止問答串流：進行中的研報要繼續跑（背景執行），不因為使用者又問了一題而消失。
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
        for await (const raw of streamAsk({ ...body, request_id: requestId, locale }, ctrl.signal)) {
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
  }, [abortAsk, qc, locale])

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

  /**
   * 訂閱一條研報事件流（新生成或重連皆走此），把事件派進 reducer。
   *
   * 串流結束卻沒有終端事件（done/error）＝連線掉了，**不代表生成失敗**——生成在伺服器
   * 背景跑，斷的只是這條訂閱。所以這裡自動重連而不是報錯：nginx 60s 讀取逾時、行動網路
   * 切換、筆電闔蓋都會打斷 SSE，而一份研報要跑 5–12 分鐘，這種事必然發生。
   * 重連次數有上限**且帶退避**：run 真的消失時（行程重啟 → 端點回 404）每次重連都會立刻
   * 失敗，沒有退避就是一個瞬間打完五輪的熱迴圈。
   */
  const REATTACH_LIMIT = 5
  const reattachDelayMs = (attempt: number) => Math.min(1000 * 2 ** attempt, 8000)
  // 重連是自呼叫。直接遞迴 consumeReport 會踩到「在宣告前存取」——用 ref 轉一手，
  // 且遞迴一定發生在非同步之後（effect 早已跑過），拿得到值。
  const consumeRef = useRef<((turnId: string, open: (s: AbortSignal) => AsyncGenerator<RawSSEEvent>, attempt?: number) => void) | null>(null)
  const consumeReport = useCallback((
    turnId: string,
    open: (signal: AbortSignal) => AsyncGenerator<RawSSEEvent>,
    attempt = 0,
  ) => {
    reportCtrl.current?.abort()
    const myReport = ++reportReqId.current
    reportTurnRef.current = turnId
    const ctrl = new AbortController()
    reportCtrl.current = ctrl
    void (async () => {
      let sawTerminal = false
      try {
        for await (const raw of open(ctrl.signal)) {
          if (myReport !== reportReqId.current) return
          const ev = parseReportEvent(raw)
          if (!ev) continue
          if (ev.event === 'run') {
            reportRunRef.current = ev.data.run_id
            // 起始時刻由「現在 − 已耗時」回推：直接送伺服器時間戳會受兩端時鐘偏差影響，
            // 而重連時 elapsed_ms 正是我們唯一需要的東西。
            dispatch({ type: 'report-event', id: turnId, event: ev, startedAt: Date.now() - ev.data.elapsed_ms })
            continue
          }
          if (ev.event === 'done' || ev.event === 'error') sawTerminal = true
          dispatch({ type: 'report-event', id: turnId, event: ev })
        }
      } catch (err) {
        if (myReport !== reportReqId.current || ctrl.signal.aborted) return
        // 429＝排隊已滿，後端沒有開任何 run，重連只會再撞一次同一堵牆。
        if (err instanceof ApiError && err.status === 429) {
          reportTurnRef.current = null
          reportRunRef.current = null
          dispatch({ type: 'report-fail', id: turnId, errorText: err.message })
          return
        }
        // 其餘連線層失敗與「串流正常結束但沒終端事件」同一個處置：試著接回去。
      }
      if (myReport !== reportReqId.current || ctrl.signal.aborted) return
      if (sawTerminal) { reportTurnRef.current = null; reportRunRef.current = null; return }
      const runId = reportRunRef.current
      if (runId && attempt < REATTACH_LIMIT) {
        setTimeout(() => {
          // 等待期間若使用者已改看別的（新生成、換對話），這次重連就作廢。
          if (myReport !== reportReqId.current) return
          consumeRef.current?.(turnId, signal => streamReportRun(runId, signal), attempt + 1)
        }, reattachDelayMs(attempt))
        return
      }
      reportTurnRef.current = null
      reportRunRef.current = null
      dispatch({
        type: 'report-fail', id: turnId,
        errorText: runId ? '連線中斷。研報可能仍在背景生成，重新整理即可接回進度' : '研報生成失敗，請重試',
      })
    })()
  }, [])
  useEffect(() => { consumeRef.current = consumeReport }, [consumeReport])

  const generateReport = useCallback((turnId: string, question: string, qaId: string | null, templateId?: string) => {
    // 另一則對話輪正在生成 → 真的把它取消掉（後端 REPORT_SEMAPHORE 本來就序列化，
    // 留著只會排隊產出一份沒人在看的研報）。背景執行後光是斷訂閱已不等於取消。
    const prevTurn = reportTurnRef.current
    const prevRun = reportRunRef.current
    if (prevTurn && prevTurn !== turnId) {
      if (prevRun) void cancelReportRun(prevRun).catch(() => { /* 取消失敗不擋新生成 */ })
      dispatch({ type: 'report-cancel', id: prevTurn })
    }
    reportRunRef.current = null
    dispatch({ type: 'report-start', id: turnId, startedAt: Date.now() })
    const body: { question: string; conversation_id?: string; qa_id?: string; template_id?: string; locale?: typeof locale } = { question, locale }
    if (convRef.current) body.conversation_id = convRef.current
    if (qaId) body.qa_id = qaId
    if (templateId) body.template_id = templateId
    consumeReport(turnId, signal => streamReport(body, signal))
  }, [consumeReport, locale])

  const cancelReport = useCallback((turnId: string, runId: string) => {
    detachReport()
    dispatch({ type: 'report-cancel', id: turnId })
    void cancelReportRun(runId).catch(() => { /* 已結束的 run 取消失敗無妨 */ })
  }, [detachReport])

  /**
   * 對話載入後，把仍在背景生成的研報接回畫面。
   *
   * 這就是「重新整理或開新分頁看不到生成的框」的修復點：狀態的真相在伺服器
   * （web/report_runs.py），不是瀏覽器分頁的記憶體，所以任何一個分頁問一下就都看得到。
   * 全程 fail-open——問不到就當作沒有進行中的生成，絕不擋住對話載入。
   */
  const attachActiveRuns = useCallback(async (conversationId: string, turns: Turn[]) => {
    let runs
    try {
      runs = await getActiveReportRuns(conversationId)
    } catch { return }
    if (convRef.current !== conversationId) return
    const run = runs[0]  // 後端序列化生成，實務上至多一個
    if (!run) return
    // qa_id 對回來源那一輪；對不上（舊 run／qa_id 為空）就掛在最後一輪，
    // 總比讓使用者完全看不到進行中的生成好。
    const turn = turns.find(t => t.id === run.qa_id) ?? turns[turns.length - 1]
    if (!turn) return
    dispatch({ type: 'report-start', id: turn.id, startedAt: Date.now() - run.elapsed_ms })
    consumeReport(turn.id, signal => streamReportRun(run.run_id, signal))
  }, [consumeReport])

  const declineReport = useCallback((turnId: string, qaId: string | null) => {
    dispatch({ type: 'report-decline', id: turnId })
    if (qaId) void setReportOffer(qaId, 'decline').catch(() => { /* 婉拒寫入失敗不打擾 */ })
  }, [])

  const restoreReportOffer = useCallback((turnId: string, qaId: string | null) => {
    dispatch({ type: 'report-reoffer', id: turnId })
    if (qaId) void setReportOffer(qaId, 'restore').catch(() => { /* 還原寫入失敗不打擾 */ })
  }, [])

  const loadConversation = useCallback(async (id: string) => {
    abortAll()
    const my = ++reqId.current
    convRef.current = id
    setConversationId(id)
    try {
      const items = await getConversation(id)
      if (my !== reqId.current) return
      const turns = items.map(turnFromHistory)
      dispatch({ type: 'load', turns })
      // 用剛算好的 turns，而不是 stateRef——後者要等 render 後的 effect 才會更新，
      // 這裡讀到的還是上一個對話的內容。
      void attachActiveRuns(id, turns)
    } catch { /* 載入失敗不破壞現況 */ }
  }, [abortAll, attachActiveRuns])

  const newConversation = useCallback(() => {
    abortAll()
    ++reqId.current
    convRef.current = null
    setConversationId(null)
    dispatch({ type: 'reset' })
  }, [abortAll])

  const setFeedback = useCallback((turnId: string, qaId: string, value: 'like' | 'dislike' | null) => {
    dispatch({ type: 'feedback', id: turnId, value })
    void sendFeedback(qaId, value ?? 'none').catch(() => { /* 回饋失敗不打擾 */ })
  }, [])

  return {
    state, conversationId, submit, stop, regenerate, editResubmit, setVersion, loadVersions,
    generateReport, declineReport, restoreReportOffer, cancelReport, loadConversation,
    newConversation, setFeedback,
  }
}
