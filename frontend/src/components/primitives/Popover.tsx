import { useEffect, useRef, type ReactNode } from 'react'
import { useFocusTrap } from '../../lib/useFocusTrap'
import styles from './Popover.module.css'

interface PopoverProps {
  open: boolean
  onClose: () => void
  children: ReactNode
  className?: string
  /** panel ARIA role；預設 'menu'（選單），表單型彈出層傳 'dialog' */
  role?: string
  ariaLabel?: string
}

export function Popover({ open, onClose, children, className, role = 'menu', ariaLabel }: PopoverProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  useFocusTrap(panelRef, open)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  return (
    <>
      <div className={styles.scrim} onClick={onClose} aria-hidden="true" />
      <div ref={panelRef} className={`${styles.panel} ${className ?? ''}`} role={role} aria-label={ariaLabel}>
        {children}
      </div>
    </>
  )
}
