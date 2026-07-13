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
import styles from './AskPage.module.css'

export default function AskPage() {
  const ctrl = useAskController()
  const { turns } = ctrl.state
  const [params, setParams] = useSearchParams()
  const c = params.get('c')
  const [draft, setDraft] = useState('')
  const [drawer, setDrawer] = useState<{ open: boolean; turnId: string | null }>({ open: false, turnId: null })
  const [modal, setModal] = useState<{ reportId: string | null; fileName?: string }>({ reportId: null })
  const flowRef = useRef<HTMLDivElement>(null)

  // URL ?c → 載入既有對話（或無 c → 新對話）
  useEffect(() => {
    if (c && c !== ctrl.conversationId) void ctrl.loadConversation(c)
    else if (!c && ctrl.conversationId !== null) ctrl.newConversation()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [c])
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
  const drawerTurn = drawer.turnId ? turns.find(t => t.id === drawer.turnId) ?? null : null

  return (
    <div className={styles.page}>
      <div className={styles.column}>
        <div className={`${styles.flow} ${turns.length === 0 ? styles.flowCentered : ''} tf-scroll`} ref={flowRef}>
          {turns.length === 0 ? (
            <AskEmptyState value={draft} onChange={setDraft} onSubmit={handleSubmit} />
          ) : (
            turns.map(t => (
              <div key={t.id} className="tf-reveal">
                <UserMessage text={t.question} onEdit={q => ctrl.editResubmit(t.id, t.qaId, q)} />
                <AssistantMessage
                  turn={t}
                  onCite={() => setDrawer({ open: true, turnId: t.id })}
                  onOpenSources={() => setDrawer(d => (d.open && d.turnId === t.id ? { open: false, turnId: t.id } : { open: true, turnId: t.id }))}
                  onFeedback={v => t.qaId && ctrl.setFeedback(t.id, t.qaId, v)}
                  onNoticeRetry={() => setDraft(t.question)}
                  onErrorRetry={() => handleSubmit(t.question)}
                  onRegenerate={() => ctrl.regenerate(t.id, t.qaId, t.question)}
                  onFollowup={q => handleSubmit(q)}
                  onSetVersion={i => {
                    ctrl.setVersion(t.id, i)
                    if (t.priorVersions.length === 0 && t.rootQaId && t.versionCount > 1) void ctrl.loadVersions(t.id, t.rootQaId)
                  }}
                />
                <DeepReportPanel
                  report={t.report}
                  onGenerate={() => ctrl.generateReport(t.id, t.question, t.qaId)}
                  onDecline={() => ctrl.declineReport(t.id)}
                />
              </div>
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
        turn={drawerTurn}
        onClose={() => setDrawer(d => ({ ...d, open: false }))}
        onOpenReport={(reportId, fileName) => setModal({ reportId, fileName })}
      />
      <ReportDetailModal reportId={modal.reportId} fileName={modal.fileName} onClose={() => setModal({ reportId: null })} />
    </div>
  )
}
