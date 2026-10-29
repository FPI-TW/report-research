import { useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router'
import { BarChart3, CircleHelp, FileText, Library, MessageSquare, Plus, Search, Settings, X, type LucideIcon } from 'lucide-react'
import { useFluidHover, useRegisterFluidHoverItem } from '@/hooks/use-fluid-hover'
import { FluidHoverHighlight } from '@/components/fluid-hover-highlight'
import { useStableRect } from '@/lib/useStableRect'
import { Modal } from '../primitives/Modal'
import { useConversations } from '../../lib/useConversations'
import { useDebouncedValue } from '../../lib/useDebouncedValue'
import { useShell } from './shellContext'
import styles from './CommandPalette.module.css'

interface Item {
  id: string
  label: string
  icon: LucideIcon
  group: '對話' | '前往'
  run: () => void
}

const GLIDE = { duration: 0.2, ease: [0.32, 0.72, 0, 1] as const }

/**
 * 搜尋彈窗（交接包「搜尋彈窗」，⌘K／Ctrl K）：上方輸入框，下方分「對話」「前往」兩組。
 * ↑↓ 移動、Enter 開啟、Esc 關閉（Esc 由 Modal 處理）。對話用後端的對話搜尋
 * （與原本側欄的搜尋框同一支 API），前往是固定的頁面與動作，依名稱比對。
 */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} bare ariaLabel="搜尋" className={styles.dialog}>
      {open && <PaletteBody onClose={onClose} />}
    </Modal>
  )
}

function Option({ register, index, item, on, onHover }: {
  register: (index: number, element: HTMLElement | null) => void
  index: number
  item: Item
  on: boolean
  onHover: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  useRegisterFluidHoverItem(register, index, ref)
  const Icon = item.icon
  return (
    <div
      ref={ref}
      id={`cmd-${item.id}`}
      role="option"
      aria-selected={on}
      className={styles.item}
      data-on={on || undefined}
      onMouseMove={onHover}
      onClick={item.run}
    >
      <Icon size={16} aria-hidden className={styles.icon} />
      <span className={styles.label}>{item.label}</span>
    </div>
  )
}

function PaletteBody({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState('')
  const [sel, setSel] = useState(0)
  const navigate = useNavigate()
  const { openSettings } = useShell()
  const q = useDebouncedValue(query.trim(), 200)
  const { items: convs } = useConversations(q)
  const listRef = useRef<HTMLDivElement>(null)
  const hover = useFluidHover(listRef)

  const items = useMemo<Item[]>(() => {
    const go = (to: string) => () => { onClose(); navigate(to) }
    const page = (id: string, label: string, icon: LucideIcon, run: () => void): Item => ({ id, label, icon, group: '前往', run })
    const pages: Item[] = [
      page('new', '新對話', Plus, go('/assistant')),
      page('library', '研報庫', Library, go('/library')),
      page('assistant', '研究助理', MessageSquare, go('/assistant')),
      page('consensus', '券商共識', BarChart3, go('/consensus')),
      page('brief', '每日簡報', FileText, go('/brief')),
      page('settings', '設定', Settings, () => { onClose(); openSettings('account') }),
      page('help', '使用說明', CircleHelp, go('/help')),
    ].filter(p => !query.trim() || p.label.includes(query.trim()))
    const conv: Item[] = convs.slice(0, 6).map((c): Item => ({
      id: `c-${c.conversation_id}`,
      label: c.title,
      icon: MessageSquare,
      group: '對話',
      run: go(`/assistant?c=${encodeURIComponent(c.conversation_id)}`),
    }))
    return [...conv, ...pages]
  }, [convs, query, navigate, onClose, openSettings])

  const cur = Math.min(sel, Math.max(items.length - 1, 0))
  const rect = useStableRect(hover, items.length ? cur : -1)

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.nativeEvent.isComposing) return
    const n = items.length
    if (e.key === 'ArrowDown' && n) { e.preventDefault(); setSel((cur + 1) % n) }
    else if (e.key === 'ArrowUp' && n) { e.preventDefault(); setSel((cur - 1 + n) % n) }
    else if (e.key === 'Enter' && n) { e.preventDefault(); items[cur].run() }
  }

  let lastGroup = ''
  return (
    <div className={styles.wrap}>
      <div className={styles.inputRow}>
        <Search size={18} strokeWidth={1.5} aria-hidden className={styles.inputIcon} />
        <input
          className={styles.input}
          type="search"
          role="combobox"
          aria-expanded="true"
          aria-controls="cmd-list"
          aria-activedescendant={items.length ? `cmd-${items[cur].id}` : undefined}
          aria-label="搜尋對話或頁面"
          placeholder="搜尋對話或頁面"
          value={query}
          maxLength={200}
          autoFocus
          onChange={e => { setQuery(e.target.value); setSel(0) }}
          onKeyDown={onKeyDown}
        />
        <button type="button" className={styles.close} aria-label="關閉" onClick={onClose}>
          <X size={14} strokeWidth={1.8} aria-hidden />
        </button>
      </div>
      <div id="cmd-list" ref={listRef} role="listbox" aria-label="搜尋結果" className={`${styles.list} tf-scroll`}>
        <FluidHoverHighlight rect={rect} session={0} fadeIn={false} transition={GLIDE} className={styles.hl} />
        {items.map((it, i) => {
          const heading = it.group !== lastGroup ? it.group : null
          lastGroup = it.group
          return (
            <div key={it.id} className={styles.itemWrap}>
              {heading && <div className={styles.group}>{heading}</div>}
              <Option register={hover.registerItem} index={i} item={it} on={i === cur} onHover={() => { if (i !== cur) setSel(i) }} />
            </div>
          )
        })}
        {items.length === 0 && <p className={styles.empty} role="status">沒有符合的結果</p>}
      </div>
    </div>
  )
}
