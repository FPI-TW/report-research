import { useEffect, useId, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Icon } from '../primitives/Icon'
import { useFocusTrap } from '../../lib/useFocusTrap'
import c from './Console.module.css'
import o from './Overlay.module.css'
import { useRestoreFocus } from './useRestoreFocus'

interface Props {
  open: boolean
  onClose: () => void
  title: ReactNode
  /** 標題用等寬字（服務名、代號）。 */
  monoTitle?: boolean
  /** 標題右側的狀態（通常是 `<Status>`）。 */
  status?: ReactNode
  subtitle?: ReactNode
  /** 寬度（px）：服務 470，其他 520。 */
  width?: number
  /** 底部固定的動作列（左邊可放一句說明，右邊放按鈕）。 */
  footer?: ReactNode
  children: ReactNode
}

/**
 * 右側抽屜：詳情用。portal 到 body，Esc 與點遮罩關閉，焦點鎖在抽屜內，關閉後還給開啟前的元素（列內的入口按鈕）。
 * 開關狀態請放 URL query（`useQueryParam`），連結才能分享。內容用 `DrawerSection` 分段。
 * 上面疊著對話框時 Esc 先關對話框（對話框會攔下事件）。
 */
export function Drawer({ open, onClose, title, monoTitle, status, subtitle, width, footer, children }: Props) {
  const ref = useRef<HTMLElement>(null)
  const titleId = useId()
  useRestoreFocus(ref, open)
  useFocusTrap(ref, open)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (document.querySelector('[data-console-scrim]')) return
      onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  return createPortal(
    <>
      <div className={o.drawerScrim} onClick={onClose} />
      <aside ref={ref} className={o.drawer} role="dialog" aria-modal="true" aria-labelledby={titleId}
        style={width ? { ['--drawer-w' as string]: `${width}px` } : undefined}>
        <div className={o.drawerHead}>
          <div className={o.drawerTitleRow}>
            <h2 id={titleId} className={`${o.drawerTitle} ${monoTitle ? o.drawerTitleMono : ''}`}>{title}</h2>
            {status}
            <button type="button" className={c.iconBtn} onClick={onClose} title="關閉" aria-label="關閉"><Icon name="x" size={16} /></button>
          </div>
          {subtitle && <div className={o.drawerSub}>{subtitle}</div>}
        </div>
        <div className={o.drawerBody}>{children}</div>
        {footer && <div className={o.drawerFoot}>{footer}</div>}
      </aside>
    </>,
    document.body,
  )
}

export function DrawerSection({ title, children }: { title?: ReactNode; children: ReactNode }) {
  return (
    <section className={o.drawerSection}>
      {title && <h3 className={o.drawerSectionTitle}>{title}</h3>}
      {children}
    </section>
  )
}

/** 抽屜底部動作列左邊的一句說明（例如「需要 ops.operate，執行前會要求重新驗證」）。 */
export function DrawerFootNote({ children }: { children: ReactNode }) {
  return <span className={o.drawerFootNote}>{children}</span>
}
