import { useEffect, useId, useRef, type ReactNode } from 'react'
import { useFocusTrap } from '../../lib/useFocusTrap'
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
  useFocusTrap(panelRef, open)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  return (
    <div className={styles.scrim} data-scrim onClick={onClose}>
      <div
        ref={panelRef}
        className={`${styles.panel} ${className ?? ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title != null ? titleId : undefined}
        onClick={e => e.stopPropagation()}
      >
        {title != null && (
          <div className={styles.header}>
            <div id={titleId} className={styles.title}>{title}</div>
            <button type="button" className={styles.close} onClick={onClose} aria-label="關閉">×</button>
          </div>
        )}
        <div className={styles.body}>{children}</div>
      </div>
    </div>
  )
}
