import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type MouseEvent } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router'
import { Icon } from '../primitives/Icon'
import c from './Console.module.css'
import o from './Overlay.module.css'

export interface RowAction {
  label: string
  onClick: () => void
  /** 危險動作：紅字、排到最後、上方加分隔線。 */
  danger?: boolean
  disabled?: boolean
  /** 停用的原因（例如「不能停用自己」），會放在 `title`。停用的項目一定要給。 */
  hint?: string
}

/** 主要動作是導頁時給 `to`：畫成真的連結（可以中鍵開新分頁），`onClick` 不用給。 */
export type RowPrimary = RowAction | { label: string; to: string; hint?: string }

interface Props {
  /** 主要動作：一顆 26px 按鈕（或連結）。沒有就只有「更多」。 */
  primary?: RowPrimary
  /** 「更多 ▾」選單的項目；空陣列或不給就不畫選單。falsy 的項目會略過，方便寫條件式。 */
  more?: readonly (RowAction | false | null | undefined)[]
  /** 選單的無障礙名稱，例如「jason.wu 的更多操作」。 */
  label?: string
}

/**
 * 表格列動作：最多一顆主要按鈕＋「更多 ▾」選單。危險項自動排到最後、上方加分隔線。
 * 點擊不會冒泡到列（整列可點開抽屜時不會誤觸）。選單 portal 到 body、用 fixed 定位，
 * 不會被表格的橫向捲動容器裁掉；Esc、點外面、捲動或視窗縮放都會關。
 */
export function RowActions({ primary, more, label }: Props) {
  const items = (more ?? []).filter((x): x is RowAction => Boolean(x))
  const ordered = [...items.filter(i => !i.danger), ...items.filter(i => i.danger)]
  const firstDanger = ordered.findIndex(i => i.danger)
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const menuId = useId()
  const stop = (e: MouseEvent) => e.stopPropagation()
  const close = useCallback((refocus = false) => {
    setOpen(false)
    if (refocus) btnRef.current?.focus()
  }, [])

  useLayoutEffect(() => {
    if (!open || !btnRef.current) return
    const r = btnRef.current.getBoundingClientRect()
    const height = menuRef.current?.offsetHeight ?? 0
    // 下面放不下就往上開。
    const below = r.bottom + 5
    const top = below + height > window.innerHeight - 8 && r.top - 5 - height > 8 ? r.top - 5 - height : below
    setPos({ top, right: Math.max(8, window.innerWidth - r.right) })
  }, [open])

  useEffect(() => {
    if (!open) return
    menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]:not(:disabled)')?.focus()
    const onDown = (e: Event) => {
      const t = e.target as Node
      if (!menuRef.current?.contains(t) && !btnRef.current?.contains(t)) close()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); close(true); return }
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
      const nodes = Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)') ?? [])
      if (nodes.length === 0) return
      e.preventDefault()
      const i = nodes.indexOf(document.activeElement as HTMLElement)
      nodes[(i + (e.key === 'ArrowDown' ? 1 : -1) + nodes.length) % nodes.length].focus()
    }
    const onScroll = () => close()
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey, true)
    window.addEventListener('resize', onScroll)
    window.addEventListener('scroll', onScroll, true)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey, true)
      window.removeEventListener('resize', onScroll)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [open, close])

  return (
    <div className={o.rowActions} onClick={stop}>
      {primary && ('to' in primary ? (
        <Link to={primary.to} className={`${c.btn} ${c.btnSm}`} title={primary.hint}>{primary.label}</Link>
      ) : (
        <button type="button" className={`${c.btn} ${c.btnSm}`} onClick={primary.onClick}
          disabled={primary.disabled} title={primary.hint}>{primary.label}</button>
      ))}
      {ordered.length > 0 && (
        <button ref={btnRef} type="button" className={`${c.btn} ${c.btnSm} ${o.moreBtn}`} aria-haspopup="menu"
          aria-expanded={open} aria-controls={open ? menuId : undefined} aria-label={label}
          onClick={() => setOpen(v => !v)}>
          更多<Icon name="chevronDown" size={12} />
        </button>
      )}
      {open && createPortal(
        <div ref={menuRef} id={menuId} className={o.menu} role="menu" aria-label={label ?? '更多操作'}
          style={pos ? { top: pos.top, right: pos.right } : { visibility: 'hidden' }} onClick={stop}>
          {ordered.map((item, i) => (
            <div key={item.label} role="none">
              {i === firstDanger && i > 0 && <div className={o.menuSep} role="separator" />}
              <button type="button" role="menuitem" className={`${o.menuItem} ${item.danger ? o.menuDanger : ''}`}
                disabled={item.disabled} title={item.hint}
                onClick={() => { close(); item.onClick() }}>{item.label}</button>
            </div>
          ))}
        </div>,
        document.body,
      )}
    </div>
  )
}
