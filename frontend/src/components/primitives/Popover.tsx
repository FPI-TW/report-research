import { useEffect, useRef, type ReactNode } from 'react'
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
}

export function Popover({ open, onClose, children, className, role = 'menu', ariaLabel, openUp = false }: PopoverProps) {
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

  return (
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
            style={{ transformOrigin: openUp ? 'bottom' : 'top' }}
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
}
