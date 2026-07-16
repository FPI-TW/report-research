import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { motion } from 'motion/react'
import { MotionLink } from '../primitives/MotionLink'
import { Icon } from '../primitives/Icon'
import { ConfirmDialog } from '../primitives/ConfirmDialog'
import { useConversations } from '../../lib/useConversations'
import { useDeleteConversation } from '../../lib/useDeleteConversation'
import styles from './ConversationList.module.css'

/** 列 hover 時 del 鈕滑入；rest 隱藏、hover/focus 顯現（取代原 CSS opacity 顯隱）。 */
const delVariants = {
  rest: { opacity: 0, x: 6 },
  hover: { opacity: 1, x: 0 },
}

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
        <MotionLink to="/ask" className={styles.newBtn} whileTap={{ scale: 0.97 }}><Icon name="plus" size={17} /> 新對話</MotionLink>
      </div>
      <div className={styles.heading}>歷史對話</div>
      <div className={`${styles.list} tf-scroll`}>
        {(data ?? []).map((cv) => (
          <motion.div key={cv.conversation_id} className={styles.row} initial="rest" animate="rest" whileHover="hover">
            <MotionLink
              to={`/ask?c=${encodeURIComponent(cv.conversation_id)}`}
              className={`${styles.item} ${cv.conversation_id === activeC ? styles.active : ''}`}
              aria-current={cv.conversation_id === activeC ? 'page' : undefined}
              title={cv.title}
              whileTap={{ scale: 0.98 }}
            >{cv.title}</MotionLink>
            <motion.button
              type="button"
              className={styles.del}
              aria-label="刪除對話"
              variants={delVariants}
              whileFocus={{ opacity: 1, x: 0 }}
              whileTap={{ scale: 0.94 }}
              onClick={() => setPending(cv.conversation_id)}
            >
              <Icon name="trash" size={15} />
            </motion.button>
          </motion.div>
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
