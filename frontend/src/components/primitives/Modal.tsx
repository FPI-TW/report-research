import { useEffect, useId, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useFocusTrap } from '../../lib/useFocusTrap'
import { TF_DUR, TF_EASE_OUT, tfInstant } from '../../lib/motionTokens'
import styles from './Modal.module.css'

interface ModalProps {
  open: boolean
  onClose: () => void
  title?: ReactNode
  children: ReactNode
  className?: string
}

export function Modal({ open, onClose, title, children, className }: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const reduced = useReducedMotion()
  useFocusTrap(panelRef, open)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // 一律 portal 到 document.body。`.scrim` 是 position: fixed，而 fixed 的定位基準
  // 不必然是視窗——祖先只要有 backdrop-filter／filter／transform 就會成為它的
  // containing block。側欄 SideRail 的 `.rail` 正是磨砂玻璃（backdrop-filter）且
  // overflow: hidden，所以歷史對話的刪除確認框原本被鎖在 272px 寬的側欄裡、
  // 貼著畫面最左側顯示並被裁切。這條缺陷不會有任何錯誤訊息，只會「窗開錯地方」。
  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div
          key="modal"
          className={styles.scrim}
          data-scrim
          onClick={onClose}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={reduced ? tfInstant : { duration: TF_DUR.d2, ease: TF_EASE_OUT }}
        >
          {/* 遮罩的底色與模糊住在這個同層子節點，不在 .scrim 上——面板一旦是
              backdrop-filter 元素的後代，它自己的模糊就整個失效。理由寫在
              Modal.module.css 的 .scrim 註解。 */}
          <div className={styles.scrimLayer} data-scrim-layer />
          <motion.div
            ref={panelRef}
            className={`${styles.panel} ${className ?? ''}`}
            role="dialog"
            aria-modal="true"
            aria-labelledby={title != null ? titleId : undefined}
            onClick={e => e.stopPropagation()}
            initial={{ opacity: 0, y: 8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={reduced ? tfInstant : { duration: TF_DUR.d3, ease: TF_EASE_OUT }}
          >
            {title != null && (
              <div className={styles.header}>
                <div id={titleId} className={styles.title}>{title}</div>
                <button type="button" className={styles.close} onClick={onClose} aria-label="關閉">×</button>
              </div>
            )}
            <div className={styles.body}>{children}</div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  )
}
