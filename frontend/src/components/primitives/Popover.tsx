import { useEffect, useRef, type CSSProperties, type ReactNode } from 'react'
import { useFocusTrap } from '../../lib/useFocusTrap'
import { usePresence } from '../../lib/usePresence'
import styles from './Popover.module.css'

interface PopoverProps {
  open: boolean
  onClose: () => void
  children: ReactNode
  className?: string
  /** panel ARIA role；預設 'menu'（選單），表單型彈出層傳 'dialog' */
  role?: string
  ariaLabel?: string
  /**
   * 進場錨點方向：決定 transform-origin 與進場位移方向，讓面板自錨點對應角落展開。
   * top＝自上緣（rise -4px）、bottom＝自下緣（rise 4px，向上開的選單用）；-end 變體再靠右緣。
   * 未設時維持預設（自上緣、rise -4px），行為與既有呼叫端完全一致。
   */
  origin?: 'top' | 'bottom' | 'top-end' | 'bottom-end'
}

/** 將 origin 映射為面板的 --tf-pop-origin／--tf-pop-rise 內聯變數；未設時回傳 undefined 沿用 CSS 預設 */
function originVars(origin?: PopoverProps['origin']): CSSProperties | undefined {
  if (!origin) return undefined
  const fromBottom = origin.startsWith('bottom')
  const base = fromBottom ? 'bottom' : 'top'
  return {
    '--tf-pop-origin': origin.endsWith('-end') ? `${base} right` : base,
    '--tf-pop-rise': fromBottom ? '4px' : '-4px',
  } as CSSProperties
}

export function Popover({ open, onClose, children, className, role = 'menu', ariaLabel, origin }: PopoverProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const { isMounted, state } = usePresence(open, { duration: 180 })
  useFocusTrap(panelRef, open)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!isMounted) return null
  return (
    <>
      <div className={styles.scrim} onClick={onClose} aria-hidden="true" />
      <div ref={panelRef} data-state={state} className={`${styles.panel} ${className ?? ''}`} style={originVars(origin)} role={role} aria-label={ariaLabel}>
        {children}
      </div>
    </>
  )
}
