import { useState } from 'react'
import type { ConversationSummary } from '../schemas'
import { ConfirmDialog } from './ConfirmDialog'
import styles from './AskPage.module.css'

interface ConversationSidebarProps {
  conversations: ConversationSummary[]
  activeId: string | null
  onOpen: (id: string) => void
  onDelete: (id: string) => void
  onNew: () => void
}

export function ConversationSidebar({ conversations, activeId, onOpen, onDelete, onNew }: ConversationSidebarProps) {
  const [pending, setPending] = useState<string | null>(null)
  return (
    <aside className={styles.sidebar}>
      <button type="button" data-testid="ask-new" className={styles.newBtn} onClick={onNew}>
        + 新對話
      </button>
      <div className={styles.histList}>
        {conversations.length === 0 ? (
          <div className={styles.histEmpty}>尚無歷史對話</div>
        ) : (
          conversations.map((c) => (
            <div
              key={c.conversation_id}
              data-testid="ask-hist-item"
              className={c.conversation_id === activeId ? `${styles.histItem} ${styles.active}` : styles.histItem}
            >
              <button type="button" data-testid="ask-hist-open" className={styles.histOpen} title={c.title} onClick={() => onOpen(c.conversation_id)}>
                {c.title}
              </button>
              <button type="button" data-testid="ask-hist-del" aria-label="刪除此對話" className={styles.histDel} onClick={() => setPending(c.conversation_id)}>
                ×
              </button>
            </div>
          ))
        )}
      </div>
      <ConfirmDialog
        opened={pending !== null}
        title="刪除此對話？"
        body="將永久移除整個對話串，無法復原。"
        confirmLabel="刪除"
        onCancel={() => setPending(null)}
        onConfirm={() => {
          if (pending) onDelete(pending)
          setPending(null)
        }}
      />
    </aside>
  )
}
