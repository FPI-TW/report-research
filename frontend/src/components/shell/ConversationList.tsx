import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { AnimatePresence, motion } from 'motion/react'
import { MotionLink } from '../primitives/MotionLink'
import { Icon } from '../primitives/Icon'
import { ConfirmDialog } from '../primitives/ConfirmDialog'
import { useConversations } from '../../lib/useConversations'
import { useDebouncedValue } from '../../lib/useDebouncedValue'
import { useDeleteConversation } from '../../lib/useDeleteConversation'
import styles from './ConversationList.module.css'

/** 列 hover 時 del 鈕滑入；rest 隱藏、hover/focus 顯現（取代原 CSS opacity 顯隱）。 */
const delVariants = {
  rest: { opacity: 0, x: 6 },
  hover: { opacity: 1, x: 0 },
}

export function ConversationList() {
  const [search, setSearch] = useState('')
  // 250ms：逐字輸入（含注音／倉頡組字過程）不要每個字都打一次 API。
  const q = useDebouncedValue(search, 250)
  const { items, isLoading, hasMore, isFetchingMore, loadMore } = useConversations(q)
  const searching = q.trim() !== ''
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
      <div className={styles.searchWrap}>
        <Icon name="search" size={14} className={styles.searchIcon} />
        <input
          type="search"
          className={styles.search}
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="搜尋提問"
          aria-label="搜尋歷史對話"
          maxLength={200}
        />
      </div>
      {del.isError && (
        // 只讓 deleteConversation throw 還不夠——沒有任何畫面反應等於仍是靜默失敗，
        // 而使用者的下一步是再按一次刪除。role="alert" 讓螢幕閱讀器也收得到。
        <div className={styles.error} role="alert">
          {del.error instanceof Error ? del.error.message : '刪除對話失敗'}
        </div>
      )}
      <div className={`${styles.list} tf-scroll`}>
        <AnimatePresence initial={false}>
        {items.map((cv) => (
          <motion.div
            key={cv.conversation_id}
            className={`${styles.row} ${cv.conversation_id === activeC ? styles.rowActive : ''}`}
            data-active={cv.conversation_id === activeC ? 'true' : undefined}
            layout="position"
            initial="rest"
            animate="rest"
            whileHover="hover"
            exit={{ opacity: 0, x: -12, transition: { duration: 0.18 } }}
          >
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
        </AnimatePresence>
        {/* 搜尋沒有結果要說出來：空白的清單看起來與「還在載入」「壞了」沒有差別。
            沒在搜尋時的空清單（全新帳號）則維持原樣不加字。 */}
        {searching && !isLoading && items.length === 0 && (
          <p className={styles.empty} role="status">沒有提問包含「{q.trim()}」的對話</p>
        )}
        {hasMore && (
          <button type="button" className={styles.more} onClick={loadMore} disabled={isFetchingMore}>
            {isFetchingMore ? '載入中…' : '載入更多'}
          </button>
        )}
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
