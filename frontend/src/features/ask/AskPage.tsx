import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type UIEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { ArrowDown } from 'lucide-react'
import { ApiError } from '../../lib/api'
import { getReportHash } from '../../lib/askApi'
import { showToast } from '../../lib/toast'
import { useAskController } from '../../lib/useAskController'
import { useConversations } from '../../lib/useConversations'
import { useFeatures } from '../../lib/useFeatures'
import type { AnswerView } from '../../lib/askReducer'
import { AskEmptyState } from './AskEmptyState'
import { TurnRow, type TurnActions } from './TurnRow'
import { SourcesDrawer } from './SourcesDrawer'
import { Composer } from './Composer'
import styles from './AskPage.module.css'

// 離底部多近算「在最新位置」：在這個距離內才自動貼底，超過就出現「回到最新」。
const NEAR_BOTTOM_PX = 90
// 第一題送出時輸入框從中央滑到底部的節奏（交接包「送出第一題」）。
const FLIP_TRANSITION = 'transform 0.62s cubic-bezier(0.32, 0.72, 0, 1)'

interface DrawerState {
  open: boolean
  view: AnswerView | null
  active: number | null
  nonce: number
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

export default function AskPage() {
  // 功能旗標（/api/features）在頁面層抓一次，輸入框與訊息經 useWebSearchPaused 讀（網搜開關）。
  useFeatures()
  const ctrl = useAskController()
  const { turns } = ctrl.state
  const hasTurns = turns.length > 0
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const c = params.get('c')
  const q = params.get('q')
  const [draft, setDraft] = useState('')
  const [drawer, setDrawer] = useState<DrawerState>({ open: false, view: null, active: null, nonce: 0 })
  const flowRef = useRef<HTMLDivElement>(null)
  const heroBoxRef = useRef<HTMLDivElement>(null)
  const dockRef = useRef<HTMLDivElement>(null)
  const flipFrom = useRef<{ cx: number; cy: number } | null>(null)
  // 貼底：ref 給捲動邏輯即時讀，state 只負責「回到最新」鈕的顯示。
  const stickRef = useRef(true)
  const [atBottom, setAtBottom] = useState(true)
  // 按「回到最新」後的平滑捲動途中，onScroll 量到的都還「不在底部」，不能因此把貼底關掉。
  const seekingRef = useRef(false)

  // 側欄也用同一份清單（同一個 queryKey），這裡不會多打一次請求。
  const { items: conversations } = useConversations('')
  const title = conversations.find(x => x.conversation_id === ctrl.conversationId)?.title
    ?? turns[0]?.question
    ?? '新對話'

  // 換到別的對話（或開新對話）時收起抽屜：裡面是上一串的來源。網址的 c 由自己同步回去的那次
  // （c 追上 conversationId）不算換對話。依前值比對而不是 effect，前值要確實拿到（交接包坑 5）。
  const [prevC, setPrevC] = useState(c)
  if (prevC !== c) {
    setPrevC(c)
    if (c !== ctrl.conversationId) setDrawer(d => (d.open ? { ...d, open: false, active: null } : d))
  }

  // URL ?c → 載入既有對話（或無 c → 新對話）
  useEffect(() => {
    if (c && c !== ctrl.conversationId) void ctrl.loadConversation(c)
    else if (!c && ctrl.conversationId !== null) ctrl.newConversation()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [c])
  // URL ?q → 預填 composer（例如閱讀頁的「就這篇提問」帶著報告名過來）。
  // 刻意不自動送出：讓使用者先看過、改過再按。預填後立刻把 q 從網址移掉，
  // 否則重整會拿舊題目蓋掉使用者已經編輯的內容。
  // set-state-in-effect 在此刻意豁免：這條 effect 做的正是規則允許的「與外部系統
  // 同步」——外部系統是 URL query，而且同一條 effect 必須把 ?q 從網址移掉（見上），
  // 兩件事不可分開。
  useEffect(() => {
    if (!q) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDraft(q)
    setParams(prev => {
      const sp = new URLSearchParams(prev)
      sp.delete('q')
      return sp
    }, { replace: true })
  }, [q, setParams])
  // conversationId → 同步 URL（首個 done 後）
  useEffect(() => {
    if (ctrl.conversationId && ctrl.conversationId !== c) setParams({ c: ctrl.conversationId }, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctrl.conversationId])

  // 貼底捲動：在最新位置時，內容每長一次就跟到底；使用者往上捲之後就不再拉他回去。
  useLayoutEffect(() => {
    const el = flowRef.current
    if (el && stickRef.current) el.scrollTop = el.scrollHeight
  }, [turns])

  // 第一題送出：底部輸入列一掛上，就從中央輸入框的位置滑下去（FLIP）。標題、聲明、建議題
  // 隨空白頁一起立即卸載，不等淡出。減少動態時直接出現在底部。
  useLayoutEffect(() => {
    const from = flipFrom.current
    const dock = dockRef.current
    flipFrom.current = null
    if (!hasTurns || !from || !dock || prefersReducedMotion()) return
    const box = dock.querySelector<HTMLElement>('[data-composer-box]') ?? dock
    const r = box.getBoundingClientRect()
    dock.style.transition = 'none'
    dock.style.transform = `translate(${from.cx - (r.left + r.width / 2)}px, ${from.cy - (r.top + r.height / 2)}px)`
    void dock.getBoundingClientRect()  // 先把起點畫上去，下一幀的 transition 才有起點可補間
    const raf = requestAnimationFrame(() => {
      dock.style.transition = FLIP_TRANSITION
      dock.style.transform = ''
    })
    // 補間還沒開始就被清掉（StrictMode 的重跑、或立刻離開頁面）時，別把輸入列留在起點。
    return () => {
      cancelAnimationFrame(raf)
      dock.style.transition = ''
      dock.style.transform = ''
    }
  }, [hasTurns])

  const onFlowScroll = useCallback((e: UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX
    if (seekingRef.current) {
      if (!near) return
      seekingRef.current = false
    }
    stickRef.current = near
    setAtBottom(near)
  }, [])

  const stickToBottom = useCallback(() => {
    stickRef.current = true
    setAtBottom(true)
  }, [])

  const scrollToEnd = () => {
    const el = flowRef.current
    if (!el) return
    seekingRef.current = true
    stickToBottom()
    if (typeof el.scrollTo === 'function') el.scrollTo({ top: el.scrollHeight, behavior: prefersReducedMotion() ? 'auto' : 'smooth' })
    else el.scrollTop = el.scrollHeight
  }

  // busy 要掃全部輪次而不是只看最後一輪：重新生成可以發生在任何一輪（含中間輪），
  // 只看最後一輪會讓「重生中間輪」時 Composer 停在送出模式（無法中斷）、其他輪的
  // 重生／編輯／追問也沒被鎖住——再點一下就開出第二條串流，把第一條擠成錯誤。
  // 問答串流同時最多一條（useAskController 的 askCtrl 單例），some() 不會多鎖。
  const busy = turns.some(t => t.phase === 'thinking' || t.phase === 'streaming')

  const { submit, editResubmit, regenerate, setFeedback, setVersion, loadVersions } = ctrl
  const handleSubmit = useCallback((question: string) => {
    // 中央輸入框只在空白頁存在：有它就是第一題，記下起點給 FLIP。
    if (heroBoxRef.current) {
      const r = heroBoxRef.current.getBoundingClientRect()
      flipFrom.current = { cx: r.left + r.width / 2, cy: r.top + r.height / 2 }
    }
    stickToBottom()
    submit(question)
    setDraft('')
  }, [submit, stickToBottom])
  const closeDrawer = useCallback(() => setDrawer(d => ({ ...d, open: false, active: null })), [])
  // 交給 TurnRow 的動作必須整包穩定，它的 memo 才擋得住串流期間的逐 token 重 render
  // （理由見 TurnRow）。controller 的函式都是 useCallback，setState 本來就穩定。
  const actions = useMemo<TurnActions>(() => ({
    editResubmit, regenerate, setFeedback, setVersion, loadVersions, stickToBottom,
    submit: handleSubmit,
    setDraft,
    openSources: (view, n) => setDrawer(d => ({ open: true, view, active: n, nonce: d.nonce + 1 })),
  }), [editResubmit, regenerate, setFeedback, setVersion, loadVersions, stickToBottom, handleSubmit])

  const openReport = useCallback(async (reportId: string) => {
    try {
      const hash = await getReportHash(reportId)
      if (!hash) throw new Error('missing file_hash')
      navigate(`/report/${hash}`)
    } catch (err) {
      showToast(err instanceof ApiError && err.status === 404 ? '這篇研報目前無法開啟' : '無法開啟閱讀頁，請稍後再試')
    }
  }, [navigate])

  const drawerOpen = drawer.open && hasTurns
  const shift = drawerOpen ? styles.shift : ''

  return (
    <div className={styles.page}>
      <header className={`${styles.header} ${hasTurns ? styles.headerLine : ''}`}>
        <h1 className={styles.title}>{title}</h1>
      </header>
      <div
        ref={flowRef}
        className={`${styles.flow} ${hasTurns ? styles.flowMasked : ''} ${shift} tf-scroll`}
        onScroll={onFlowScroll}
      >
        {hasTurns ? (
          <div className={styles.turns}>
            {turns.map((t, i) => <TurnRow key={t.id} turn={t} busy={busy} isLast={i === turns.length - 1} actions={actions} />)}
          </div>
        ) : (
          <AskEmptyState value={draft} onChange={setDraft} onSubmit={handleSubmit} boxRef={heroBoxRef} />
        )}
      </div>
      {hasTurns && (
        <div className={`${styles.dockLayer} ${shift}`}>
          <div ref={dockRef} className={styles.dock}>
            <button
              type="button"
              className={`${styles.toBottom} ${atBottom ? styles.toBottomOff : ''}`}
              onClick={scrollToEnd}
              aria-hidden={atBottom}
              tabIndex={atBottom ? -1 : 0}
            >
              <ArrowDown size={14} strokeWidth={2} aria-hidden />
              {busy ? '有新內容' : '回到最新'}
            </button>
            <Composer value={draft} onChange={setDraft} onSubmit={handleSubmit} disabled={busy} onStop={ctrl.stop} />
          </div>
        </div>
      )}
      <SourcesDrawer
        open={drawerOpen}
        view={drawer.view}
        active={drawer.active}
        nonce={drawer.nonce}
        onClose={closeDrawer}
        onOpenReport={openReport}
      />
    </div>
  )
}
