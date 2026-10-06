import { useEffect, useRef, type CSSProperties, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useFocusTrap } from '../../lib/useFocusTrap'
import { TF_DUR, TF_EASE_OUT, tfInstant } from '../../lib/motionTokens'
import styles from './Popover.module.css'

interface PopoverProps {
  open: boolean
  onClose: () => void
  children: ReactNode
  className?: string
  /** panel ARIA role；預設 'menu'（選單），表單型彈出層傳 'dialog' */
  role?: string
  ariaLabel?: string
  /** 向上開啟（面板在觸發點上方）：以底邊為縮放原點、自下方浮現。 */
  openUp?: boolean
  /**
   * 渲染到 document.body，跳出祖先的裁切。觸發點在 `overflow: hidden` 且帶 backdrop-filter 的
   * 容器裡（收合側欄、手機分頁列）時需要：backdrop-filter 會讓 `position: fixed` 的子孫以該容器
   * 為定位基準，仍被它裁掉，只有離開那棵 DOM 子樹才逃得出去。定位由呼叫端以 `style` 給。
   */
  portal?: boolean
  /** 面板的額外樣式（portal 時用來給 fixed 座標與寬度）。 */
  style?: CSSProperties
}

export function Popover({
  open, onClose, children, className, role = 'menu', ariaLabel, openUp = false, portal = false, style,
}: PopoverProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const reduced = useReducedMotion()
  useFocusTrap(panelRef, open)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // 進場位移方向：向下開的選單自上方 4px 落下，向上開的自下方 4px 浮起。
  const rise = openUp ? 4 : -4

  const layer = (
    <>
      {open && <div className={styles.scrim} onClick={onClose} aria-hidden="true" />}
      <AnimatePresence>
        {open && (
          <motion.div
            key="popover"
            ref={panelRef}
            className={`${styles.panel} ${className ?? ''}`}
            role={role}
            aria-label={ariaLabel}
            style={{ ...style, transformOrigin: openUp ? 'bottom' : 'top' }}
            initial={{ opacity: 0, y: rise, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: rise, scale: 0.98 }}
            transition={reduced ? tfInstant : { duration: TF_DUR.d2, ease: TF_EASE_OUT }}
          >
            {children}
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
  return portal ? createPortal(layer, document.body) : layer
}
