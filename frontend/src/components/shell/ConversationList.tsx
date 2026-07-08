import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { Icon } from '../primitives/Icon'
import { ConfirmDialog } from '../primitives/ConfirmDialog'
import { useConversations } from '../../lib/useConversations'
import { useDeleteConversation } from '../../lib/useDeleteConversation'
import styles from './ConversationList.module.css'

export function ConversationList() {
  const { data } = useConversations()
  const [params] = useSearchParams()
  const activeC = params.get('c')
  const navigate = useNavigate()
  const del = useDeleteConversation()
  const [pending, setPending] = useState<string | null>(null)

  function confirmDelete() {
    const id = pending
    if (!id) return
    setPending(null)
    del.mutate(id, { onSuccess: () => { if (id === activeC) navigate('/ask') } })
  }

  return (
    <>
      <div className={styles.newWrap}>
        <Link to="/ask" className={styles.newBtn}><Icon name="plus" size={17} /> 新對話</Link>
      </div>
      <div className={styles.heading}>歷史對話</div>
      <div className={`${styles.list} tf-scroll`}>
        {(data ?? []).map((cv) => (
          <div key={cv.conversation_id} className={styles.row}>
            <Link
              to={`/ask?c=${encodeURIComponent(cv.conversation_id)}`}
              className={`${styles.item} ${cv.conversation_id === activeC ? styles.active : ''}`}
              aria-current={cv.conversation_id === activeC ? 'page' : undefined}
              title={cv.title}
            >{cv.title}</Link>
            <button type="button" className={styles.del} aria-label="刪除對話" onClick={() => setPending(cv.conversation_id)}>
              <Icon name="trash" size={15} />
            </button>
          </div>
        ))}
      </div>
      <ConfirmDialog
        open={pending !== null}
        title="刪除此對話？"
        body="將永久移除整個對話串，無法復原。"
        confirmLabel="刪除"
        onConfirm={confirmDelete}
        onCancel={() => setPending(null)}
      />
    </>
  )
}
