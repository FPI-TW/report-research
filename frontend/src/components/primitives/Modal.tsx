import { useEffect, useId, useRef, type ReactNode } from 'react'
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

  return (
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
    </AnimatePresence>
  )
}
