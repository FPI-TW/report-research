import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { AnimatePresence, motion } from 'motion/react'
import { ChevronDown, Plus, Trash2 } from 'lucide-react'
import { ConfirmDialog } from '../primitives/ConfirmDialog'
import { useConversations } from '../../lib/useConversations'
import { useDeleteConversation } from '../../lib/useDeleteConversation'
import styles from './ConversationList.module.css'

const OPEN_KEY = 'tf.sidebar.recents'

function readOpen(): boolean {
  try { return localStorage.getItem(OPEN_KEY) !== '0' } catch { return true }
}

/** 列 hover 時刪除鈕淡入；rest 隱藏、hover/focus 顯現。 */
const delVariants = {
  rest: { opacity: 0 },
  hover: { opacity: 1 },
}

/**
 * 側欄的「最近」對話（交接包「側欄」）。標題列右側的 ＋ 開新對話（新對話入口只在這裡與搜尋彈窗，
 * 研究助理頁右上不放）；標題可收合清單。搜尋對話改在搜尋彈窗（⌘K）裡做，這裡不再放搜尋框。
 */
export function ConversationList() {
  const [open, setOpen] = useState(readOpen)
  const { items, hasMore, isFetchingMore, loadMore } = useConversations('')
  const [params] = useSearchParams()
  const activeC = params.get('c')
  const navigate = useNavigate()
  const del = useDeleteConversation()
  const [pending, setPending] = useState<string | null>(null)

  function toggle() {
    setOpen(o => {
      try { localStorage.setItem(OPEN_KEY, o ? '0' : '1') } catch { /* ignore */ }
      return !o
    })
  }

  function confirmDelete() {
    const id = pending
    if (!id) return
    setPending(null)
    del.mutate(id, { onSuccess: () => { if (id === activeC) navigate('/assistant') } })
  }

  return (
    <>
      <div className={styles.head}>
        <button type="button" className={styles.heading} aria-expanded={open} onClick={toggle}>
          最近
          <ChevronDown size={12} strokeWidth={2} aria-hidden className={styles.chev} data-open={open} />
        </button>
        <Link to="/assistant" className={styles.newBtn} aria-label="新對話" title="新對話">
          <Plus size={14} strokeWidth={1.8} aria-hidden />
        </Link>
      </div>
      {del.isError && (
        // 只讓 deleteConversation throw 還不夠——沒有任何畫面反應等於仍是靜默失敗，
        // 而使用者的下一步是再按一次刪除。role="alert" 讓螢幕閱讀器也收得到。
        <div className={styles.error} role="alert">
          {del.error instanceof Error ? del.error.message : '刪除對話失敗'}
        </div>
      )}
      {open && (
        <div className={`${styles.list} tf-scroll`}>
          <AnimatePresence initial={false}>
            {items.map((cv) => {
              const on = cv.conversation_id === activeC
              return (
                <motion.div
                  key={cv.conversation_id}
                  className={`${styles.row} ${on ? styles.rowActive : ''}`}
                  data-active={on ? 'true' : undefined}
                  layout="position"
                  initial="rest"
                  animate="rest"
                  whileHover="hover"
                  exit={{ opacity: 0, x: -12, transition: { duration: 0.18 } }}
                >
                  <Link
                    to={`/assistant?c=${encodeURIComponent(cv.conversation_id)}`}
                    className={`${styles.item} ${on ? styles.active : ''}`}
                    aria-current={on ? 'page' : undefined}
                    title={cv.title}
                  >{cv.title}</Link>
                  <motion.button
                    type="button"
                    className={styles.del}
                    aria-label="刪除對話"
                    variants={delVariants}
                    whileFocus={{ opacity: 1 }}
                    onClick={() => setPending(cv.conversation_id)}
                  >
                    <Trash2 size={14} strokeWidth={1.6} aria-hidden />
                  </motion.button>
                </motion.div>
              )
            })}
          </AnimatePresence>
          {hasMore && (
            <button type="button" className={styles.more} onClick={loadMore} disabled={isFetchingMore}>
              {isFetchingMore ? '載入中…' : '載入更多'}
            </button>
          )}
        </div>
      )}
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
