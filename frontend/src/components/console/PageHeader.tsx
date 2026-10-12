import { useId, useState, type ReactNode } from 'react'
import { useLocation } from 'react-router'
import { ADMIN_IA, locate } from '../shell/adminNav'
import { Icon } from '../primitives/Icon'
import c from './Console.module.css'

interface Props {
  /** 一句摘要（13px）：講這一頁現在的狀況或用途，不寫說明文。 */
  summary?: ReactNode
  /** 長說明：收在「說明 ▾」後面，展開成 sunk 底色的說明框。 */
  help?: ReactNode
  /** 右側的頁面動作（32px 按鈕、分段）。 */
  actions?: ReactNode
  /** 預設取導覽結構裡這一頁的名稱與「領域 / 分組」；詳情頁或不在導覽裡的頁才需要自己給。 */
  title?: ReactNode
  kicker?: ReactNode
}

/** 頁首：kicker（等寬 11px，領域 / 分組）＋ H1 22px/600 ＋一句摘要；長說明收進「說明 ▾」。 */
export function PageHeader({ summary, help, actions, title, kicker }: Props) {
  const { pathname } = useLocation()
  const here = locate(ADMIN_IA, pathname)
  const [open, setOpen] = useState(false)
  const helpId = useId()
  const autoKicker = here ? [here.domain.label, here.group.label].filter(Boolean).join(' / ') : null
  return (
    <>
      <header className={c.header}>
        <div className={c.headerMain}>
          {(kicker ?? autoKicker) && <div className={c.kicker}>{kicker ?? autoKicker}</div>}
          <h1 className={c.h1}>{title ?? here?.page.label ?? '管理後台'}</h1>
          {(summary || help) && (
            <p className={c.summary}>
              {summary}
              {help && (
                <button type="button" className={c.helpToggle} aria-expanded={open} aria-controls={helpId}
                  onClick={() => setOpen(v => !v)}>
                  說明<Icon name="chevronDown" size={12} />
                </button>
              )}
            </p>
          )}
        </div>
        {actions && <div className={c.headerActions}>{actions}</div>}
      </header>
      {help && open && <div id={helpId} className={c.help}>{help}</div>}
    </>
  )
}
