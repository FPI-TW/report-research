import { useEffect, useId, useRef, useState } from 'react'
import { Icon } from '../primitives/Icon'
import { useRestoreFocus } from '../console/useRestoreFocus'
import { useFocusTrap } from '../../lib/useFocusTrap'
import { searchPages, type AdminPageEntry } from './adminNav'
import styles from './AdminPalette.module.css'

interface Props {
  open: boolean
  /** 看得到的頁面（已依 scope 濾過）；搜尋不會找到沒有權限的頁。 */
  pages: readonly AdminPageEntry[]
  onClose: () => void
  onGo: (to: string) => void
}

/**
 * ⌘K／Ctrl+K 的頁面搜尋。比對頁名與「領域 / 分組」；Enter 跳目前選到的那一筆（預設第一筆），
 * 上下鍵移動，Esc 或點遮罩關閉，關閉後焦點還給開啟前的元素。開關的快捷鍵在 `AdminShell`。
 */
export function AdminPalette({ open, pages, onClose, onGo }: Props) {
  if (!open) return null
  // 每次打開都是新的一份（關掉時 unmount），查詢字與選取不必手動歸零。
  return <PalettePanel pages={pages} onClose={onClose} onGo={onGo} />
}

function PalettePanel({ pages, onClose, onGo }: Omit<Props, 'open'>) {
  const [query, setQuery] = useState('')
  const [index, setIndex] = useState(0)
  const panelRef = useRef<HTMLDivElement>(null)
  const listId = useId()
  useRestoreFocus(panelRef, true)
  useFocusTrap(panelRef, true)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const results = searchPages(pages, query)
  const active = Math.min(index, Math.max(0, results.length - 1))
  const optionId = (i: number) => `${listId}-${i}`

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setIndex(results.length ? (active + 1) % results.length : 0) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setIndex(results.length ? (active - 1 + results.length) % results.length : 0) }
    else if (e.key === 'Enter' && results[active]) { e.preventDefault(); onGo(results[active].page.to) }
  }

  return (
    <div className={styles.scrim} onClick={onClose}>
      <div ref={panelRef} className={styles.panel} role="dialog" aria-modal="true" aria-label="搜尋頁面"
        onClick={e => e.stopPropagation()}>
        <div className={styles.inputRow}>
          <Icon name="search" size={16} />
          <input
            className={styles.input}
            value={query}
            onChange={e => { setQuery(e.target.value); setIndex(0) }}
            onKeyDown={onKeyDown}
            placeholder="搜尋頁面"
            role="combobox"
            aria-label="搜尋頁面"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={results[active] ? optionId(active) : undefined}
            autoComplete="off"
            spellCheck={false}
          />
          <kbd className={styles.kbd}>esc</kbd>
        </div>
        <div id={listId} className={styles.list} role="listbox" aria-label="頁面">
          {results.map((r, i) => (
            <div
              key={r.page.key}
              id={optionId(i)}
              role="option"
              aria-selected={i === active}
              className={`${styles.option} ${i === active ? styles.optionOn : ''}`}
              onMouseMove={() => setIndex(i)}
              onClick={() => onGo(r.page.to)}
            >
              <span className={styles.optionLabel}>{r.page.label}</span>
              <span className={styles.optionPath}>{r.path}</span>
            </div>
          ))}
          {results.length === 0 && <div className={styles.none}>找不到這個頁面</div>}
        </div>
      </div>
    </div>
  )
}
