import { useCallback, useEffect, useRef, useState } from 'react'
import { useAskStream } from './hooks/useAskStream'
import { useConversations } from './hooks/useConversations'
import { useReportStream } from './hooks/useReportStream'
import { getConversation } from './api'
import type { TurnState } from './lib/conversation'
import { Turn } from './components/Turn'
import { AskComposer } from './components/AskComposer'
import { ConversationSidebar } from './components/ConversationSidebar'
import { ReportDetailModal } from '../search/components/ReportDetailModal'
import { ReportFullModal } from './components/ReportFullModal'
import styles from './components/AskPage.module.css'

const EXAMPLES = ['台積電最新展望如何？', 'AI 伺服器散熱有哪些重點？', '近期半導體產業趨勢']

export default function AskPage() {
  const ask = useAskStream()
  const convos = useConversations()
  const reportStream = useReportStream()
  const { refresh: refreshConvos } = convos
  const { conversationId, streaming, turns, send, loadConversation, newConversation, appendTurnReport } = ask
  const { report, start: startReport, cancel: cancelReport } = reportStream
  const [modalId, setModalId] = useState<string | null>(null)
  const [fullReportId, setFullReportId] = useState<string | null>(null)
  const wasStreaming = useRef(false)

  // 串流由 true→false（一輪結束）後刷新側欄對話清單（refreshConvos 為穩定 useCallback）
  useEffect(() => {
    if (wasStreaming.current && !streaming) refreshConvos()
    wasStreaming.current = streaming
  }, [streaming, refreshConvos])

  // 研報完成後持久化到該輪的 turn.reports，讓 ReportPanel 的歷史重播分支（優先序 1）
  // 永久保留完成卡片，不受共享 live report 狀態後續轉移到別輪影響（appendTurnReport 內建去重冪等）。
  useEffect(() => {
    if (report.phase === 'done' && report.done && report.turnId) {
      appendTurnReport(report.turnId, {
        report_id: report.done.report_id,
        title: report.done.title,
        download_url: report.done.download_url,
        created_at: null,
      })
    }
  }, [report.phase, report.done, report.turnId, appendTurnReport])

  const openConversation = useCallback(
    async (id: string) => {
      cancelReport() // 切對話中止進行中研報
      try {
        const items = await getConversation(id)
        loadConversation(items, id)
      } catch {
        /* 載入失敗不破壞現況 */
      }
    },
    [cancelReport, loadConversation],
  )

  const handleNewConversation = useCallback(() => {
    cancelReport() // 切對話中止進行中研報
    newConversation()
  }, [cancelReport, newConversation])

  const handleStartReport = useCallback(
    (turn: TurnState) => {
      startReport(turn.id, {
        question: turn.q,
        conversation_id: conversationId,
        qa_id: turn.qaId,
      })
    },
    [startReport, conversationId],
  )

  return (
    <div className={styles.page}>
      <ConversationSidebar
        conversations={convos.conversations}
        activeId={conversationId}
        onOpen={openConversation}
        onDelete={convos.remove}
        onNew={handleNewConversation}
      />
      <main className={styles.main}>
        <div className={styles.thread} data-testid="ask-thread">
          {turns.length === 0 ? (
            <div className={styles.empty} data-testid="ask-empty">
              <div className={styles.emptyTitle}>有什麼想問的？</div>
            </div>
          ) : (
            turns.map((t) => (
              <Turn
                key={t.id}
                turn={t}
                onCite={setModalId}
                report={report}
                onStartReport={() => handleStartReport(t)}
                onDismissReport={() => {}}
                onOpenFull={setFullReportId}
              />
            ))
          )}
        </div>
        <AskComposer onSend={send} disabled={streaming} examples={turns.length === 0 ? EXAMPLES : []} />
      </main>
      <ReportDetailModal reportId={modalId} onClose={() => setModalId(null)} />
      <ReportFullModal
        reportId={fullReportId}
        opened={fullReportId != null}
        onClose={() => setFullReportId(null)}
      />
    </div>
  )
}
