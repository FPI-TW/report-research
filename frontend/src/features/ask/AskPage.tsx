import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useAskController } from '../../lib/useAskController'
import { AskEmptyState } from './AskEmptyState'
import { TurnRow, type TurnActions } from './TurnRow'
import { SourcesDrawer } from './SourcesDrawer'
import { Composer } from './Composer'
import { ReportDetailModal } from '../../components/ReportDetailModal'
import type { AnswerView } from '../../lib/askReducer'
import styles from './AskPage.module.css'

export default function AskPage() {
  const ctrl = useAskController()
  const { turns } = ctrl.state
  const [params, setParams] = useSearchParams()
  const c = params.get('c')
  const q = params.get('q')
  const [draft, setDraft] = useState('')
  const [drawer, setDrawer] = useState<{ open: boolean; view: AnswerView | null }>({ open: false, view: null })
  const [modal, setModal] = useState<{ reportId: string | null; fileName?: string }>({ reportId: null })
  const flowRef = useRef<HTMLDivElement>(null)

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
  // 兩件事不可分開。要真的移掉 setDraft 得改成「以 q 當 composer 的受控初值」，
  // 那是 composer 的行為改動、需要人眼驗過，不屬於「把 lint 接進 CI」這件事。
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
  // 貼底捲動：使用者在底部附近才自動貼底
  useEffect(() => {
    const el = flowRef.current
    if (!el) return
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 120) el.scrollTop = el.scrollHeight
  }, [turns])

  // busy 要掃全部輪次而不是只看最後一輪：重新生成可以發生在任何一輪（含中間輪），
  // 只看最後一輪會讓「重生中間輪」時 Composer 停在送出模式（無法中斷）、其他輪的
  // 重生／編輯／追問也沒被鎖住——再點一下就開出第二條串流，把第一條擠成錯誤。
  // 問答串流同時最多一條（useAskController 的 askCtrl 單例），some() 不會多鎖。
  const busy = turns.some(t => t.phase === 'thinking' || t.phase === 'streaming')

  const { submit, editResubmit, regenerate, setFeedback, setVersion, loadVersions } = ctrl
  const handleSubmit = useCallback((q: string) => { submit(q); setDraft('') }, [submit])
  // 交給 TurnRow 的動作必須整包穩定，它的 memo 才擋得住串流期間的逐 token 重 render
  // （理由見 TurnRow）。controller 的函式都是 useCallback，setState 本來就穩定。
  const actions = useMemo<TurnActions>(() => ({
    editResubmit, regenerate, setFeedback, setVersion, loadVersions,
    submit: handleSubmit,
    setDraft,
    openSources: view => setDrawer({ open: true, view }),
    toggleSources: view => setDrawer(d => d.open ? { open: false, view: null } : { open: true, view }),
  }), [editResubmit, regenerate, setFeedback, setVersion, loadVersions, handleSubmit])

  return (
    <div className={styles.page}>
      <div className={styles.column}>
        <div className={`${styles.flow} ${turns.length === 0 ? styles.flowCentered : ''} tf-scroll`} ref={flowRef}>
          {turns.length === 0 ? (
            <AskEmptyState value={draft} onChange={setDraft} onSubmit={handleSubmit} />
          ) : (
            turns.map(t => <TurnRow key={t.id} turn={t} busy={busy} actions={actions} />)
          )}
        </div>
        {turns.length > 0 && (
          <div className={styles.composerBar}>
            <Composer value={draft} onChange={setDraft} onSubmit={handleSubmit} disabled={busy} onStop={ctrl.stop} />
          </div>
        )}
      </div>
      <SourcesDrawer
        open={drawer.open}
        view={drawer.view}
        onClose={() => setDrawer({ open: false, view: null })}
        onOpenReport={(reportId, fileName) => setModal({ reportId, fileName })}
      />
      <ReportDetailModal reportId={modal.reportId} fileName={modal.fileName} onClose={() => setModal({ reportId: null })} />
    </div>
  )
}
