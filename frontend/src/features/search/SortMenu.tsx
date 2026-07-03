import { useRef, useState } from 'react'
import { Icon } from '../../components/primitives/Icon'
import { Popover } from '../../components/primitives/Popover'
import { MenuItem } from '../../components/primitives/Menu'
import { sortOptions } from '../../lib/sortForMode'
import type { SearchMode, SortValue } from '../../lib/searchFilters'
import styles from './SortMenu.module.css'

interface Props { mode: SearchMode; value: SortValue; onChange: (v: SortValue) => void }

export function SortMenu({ mode, value, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const opts = sortOptions(mode)
  const current = opts.find(o => o.value === value) ?? opts[0]
  return (
    <div className={styles.wrap} ref={ref}>
      <button type="button" className={styles.trigger} onClick={() => setOpen(o => !o)}
        aria-haspopup="menu" aria-expanded={open}>
        排序：{current.label}<Icon name="chevronDown" size={14} />
      </button>
      <Popover open={open} onClose={() => setOpen(false)} className={styles.menu}>
        {opts.map(o => (
          <MenuItem key={o.value} onClick={() => { onChange(o.value); setOpen(false) }}>{o.label}</MenuItem>
        ))}
      </Popover>
    </div>
  )
}
