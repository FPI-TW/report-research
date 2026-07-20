import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useAskController } from '../../lib/useAskController'
import { AskEmptyState } from './AskEmptyState'
import { UserMessage } from './UserMessage'
import { AssistantMessage } from './AssistantMessage'
import { DeepReportPanel } from './DeepReportPanel'
import { SourcesDrawer } from './SourcesDrawer'
import { Composer } from './Composer'
import { ReportDetailModal } from '../../components/ReportDetailModal'
import { Reveal } from '../../components/primitives/Reveal'
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
  useEffect(() => {
    if (!q) return
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

  const last = turns[turns.length - 1]
  const busy = !!last && (last.phase === 'thinking' || last.phase === 'streaming')

  function handleSubmit(q: string) { ctrl.submit(q); setDraft('') }
  function openSources(view: AnswerView) {
    setDrawer({ open: true, view })
  }

  return (
    <div className={styles.page}>
      <div className={styles.column}>
        <div className={`${styles.flow} ${turns.length === 0 ? styles.flowCentered : ''} tf-scroll`} ref={flowRef}>
          {turns.length === 0 ? (
            <AskEmptyState value={draft} onChange={setDraft} onSubmit={handleSubmit} />
          ) : (
            turns.map(t => (
              <Reveal key={t.id}>
                <UserMessage text={t.question} onEdit={q => ctrl.editResubmit(t.id, t.qaId, q)} disabled={busy} />
                <AssistantMessage
                  turn={t}
                  onCite={(_n, view) => openSources(view)}
                  onOpenSources={view => setDrawer(d => d.open ? { open: false, view: null } : { open: true, view })}
                  onFeedback={v => t.qaId && ctrl.setFeedback(t.id, t.qaId, v)}
                  onNoticeRetry={() => setDraft(t.question)}
                  onErrorRetry={() => handleSubmit(t.question)}
                  onRegenerate={() => ctrl.regenerate(t.id, t.qaId, t.question)}
                  onFollowup={q => handleSubmit(q)}
                  disabled={busy}
                  onSetVersion={i => {
                    if (t.priorVersions.length === 0 && t.rootQaId && t.versionCount > 1) {
                      void ctrl.loadVersions(t.id, t.rootQaId).then(loaded => {
                        if (loaded) ctrl.setVersion(t.id, i)
                      })
                    } else {
                      ctrl.setVersion(t.id, i)
                    }
                  }}
                />
                <DeepReportPanel
                  report={t.report}
                  onGenerate={() => ctrl.generateReport(t.id, t.question, t.qaId)}
                  onDecline={() => ctrl.declineReport(t.id)}
                />
              </Reveal>
            ))
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
