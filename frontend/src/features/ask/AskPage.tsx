import { useEffect, useRef, useState } from 'react'
import { useAskStream } from './hooks/useAskStream'
import { useConversations } from './hooks/useConversations'
import { getConversation } from './api'
import { Turn } from './components/Turn'
import { AskComposer } from './components/AskComposer'
import { ConversationSidebar } from './components/ConversationSidebar'
import { ReportDetailModal } from '../search/components/ReportDetailModal'
import styles from './components/AskPage.module.css'

const EXAMPLES = ['台積電最新展望如何？', 'AI 伺服器散熱有哪些重點？', '近期半導體產業趨勢']

export default function AskPage() {
  const ask = useAskStream()
  const convos = useConversations()
  const { refresh: refreshConvos } = convos
  const [modalId, setModalId] = useState<string | null>(null)
  const wasStreaming = useRef(false)

  // 串流由 true→false（一輪結束）後刷新側欄對話清單（refreshConvos 為穩定 useCallback）
  useEffect(() => {
    if (wasStreaming.current && !ask.streaming) refreshConvos()
    wasStreaming.current = ask.streaming
  }, [ask.streaming, refreshConvos])

  const openConversation = async (id: string) => {
    try {
      const items = await getConversation(id)
      ask.loadConversation(items, id)
    } catch {
      /* 載入失敗不破壞現況 */
    }
  }

  return (
    <div className={styles.page}>
      <ConversationSidebar
        conversations={convos.conversations}
        activeId={ask.conversationId}
        onOpen={openConversation}
        onDelete={convos.remove}
        onNew={ask.newConversation}
      />
      <main className={styles.main}>
        <div className={styles.thread} data-testid="ask-thread">
          {ask.turns.length === 0 ? (
            <div className={styles.empty} data-testid="ask-empty">
              <div className={styles.emptyTitle}>有什麼想問的？</div>
            </div>
          ) : (
            ask.turns.map((t) => <Turn key={t.id} turn={t} onCite={setModalId} />)
          )}
        </div>
        <AskComposer
          onSend={ask.send}
          disabled={ask.streaming}
          examples={ask.turns.length === 0 ? EXAMPLES : []}
        />
      </main>
      <ReportDetailModal reportId={modalId} onClose={() => setModalId(null)} />
    </div>
  )
}
