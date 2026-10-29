import { useState } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router'
import { AnimatePresence, motion } from 'motion/react'
import { ChevronDown, Plus, Trash2 } from 'lucide-react'
import { ConfirmDialog } from '../primitives/ConfirmDialog'
import { useConversations } from '../../lib/useConversations'
import { usePendingConversations } from '../../lib/pendingConversation'
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

interface Row {
  key: string
  /** null＝剛送出的新對話，後端還沒建立這一串（見 pendingConversation.ts）。 */
  id: string | null
  title: string
  /** 研究助理頁上正在跑的那一串（還沒有 id、網址也還沒有 ?c）。 */
  current?: boolean
}

/**
 * 側欄的「最近」對話（交接包「側欄」）。標題列右側的 ＋ 開新對話（新對話入口只在這裡與搜尋彈窗，
 * 研究助理頁右上不放）；標題可收合清單。搜尋對話改在搜尋彈窗（⌘K）裡做，這裡不再放搜尋框。
 *
 * 新對話送出第一題就排到最上面（暫存列），不等那一輪跑完；清單重抓到真的那一列之後由它接手。
 * 沒跑完的也照樣落庫（useAskController 的 keepUnfinished），所以暫存列最後一定會換成真列或消失。
 */
export function ConversationList() {
  const [open, setOpen] = useState(readOpen)
  const { items, hasMore, isFetchingMore, loadMore } = useConversations('')
  const drafts = usePendingConversations()
  const [params] = useSearchParams()
  const { pathname } = useLocation()
  const activeC = params.get('c')
  const known = new Set(items.map(cv => cv.conversation_id))
  const rows: Row[] = [
    ...drafts
      .filter(p => !(p.conversationId && known.has(p.conversationId)))
      // 拿到 conversation_id 之後就以它為 key：重抓回來的真列沿用同一個 key，不會閃一下。
      // 只有最新那筆可能是正在跑的那一串；較舊、還沒有 id 的是切走後正在補存的。
      .map((p, i) => ({ key: p.conversationId ?? `pending:${p.key}`, id: p.conversationId, title: p.title, current: i === 0 && drafts[0] === p })),
    ...items.map(cv => ({ key: cv.conversation_id, id: cv.conversation_id, title: cv.title })),
  ]
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
            {rows.map(({ key, id, title, current }) => {
              const on = id ? id === activeC : Boolean(current) && !activeC && pathname === '/assistant'
              return (
                <motion.div
                  key={key}
                  className={`${styles.row} ${on ? styles.rowActive : ''}`}
                  data-active={on ? 'true' : undefined}
                  layout="position"
                  initial="rest"
                  animate="rest"
                  whileHover="hover"
                  // 暫存列換成真列（key 從 pending: 換成 id）時是原地替換，不播刪除的淡出。
                  exit={id ? { opacity: 0, x: -12, transition: { duration: 0.18 } } : undefined}
                >
                  <Link
                    to={id ? `/assistant?c=${encodeURIComponent(id)}` : '/assistant'}
                    className={`${styles.item} ${on ? styles.active : ''}`}
                    aria-current={on ? 'page' : undefined}
                    title={title}
                  >{title}</Link>
                  {id && (
                    <motion.button
                      type="button"
                      className={styles.del}
                      aria-label="刪除對話"
                      variants={delVariants}
                      whileFocus={{ opacity: 1 }}
                      onClick={() => setPending(id)}
                    >
                      <Trash2 size={14} strokeWidth={1.6} aria-hidden />
                    </motion.button>
                  )}
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
