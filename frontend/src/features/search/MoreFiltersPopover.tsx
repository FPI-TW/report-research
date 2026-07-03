import { useState } from 'react'
import { Popover } from '../../components/primitives/Popover'
import { activeAdvancedCount, type SearchState } from '../../lib/searchFilters'
import styles from './MoreFiltersPopover.module.css'

interface Props {
  state: SearchState
  instrumentOptions: string[]
  reportTypeOptions: string[]
  onPatch: (p: Partial<SearchState>) => void
  onClear: () => void
}

export function MoreFiltersPopover({ state, instrumentOptions, reportTypeOptions, onPatch, onClear }: Props) {
  const [open, setOpen] = useState(false)
  const count = activeAdvancedCount(state)
  return (
    <div className={styles.wrap}>
      <button type="button" className={styles.trigger} onClick={() => setOpen(o => !o)}
        aria-haspopup="menu" aria-expanded={open}>
        更多篩選{count > 0 && <span className={styles.badge}>{count}</span>}
      </button>
      <Popover open={open} onClose={() => setOpen(false)} className={styles.menu}>
        <label className={styles.field}>
          <span>商品類型</span>
          <select aria-label="商品類型" value={state.instrument_type}
            onChange={e => onPatch({ instrument_type: e.target.value })}>
            <option value="">全部</option>
            {instrumentOptions.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
        </label>
        <label className={styles.field}>
          <span>報告類型</span>
          <select aria-label="報告類型" value={state.report_type}
            onChange={e => onPatch({ report_type: e.target.value })}>
            <option value="">全部</option>
            {reportTypeOptions.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
        </label>
        <label className={styles.check}>
          <input type="checkbox" aria-label="只看個股相關" checked={state.relates_stock}
            onChange={e => onPatch({ relates_stock: e.target.checked })} />只看個股相關
        </label>
        <label className={styles.check}>
          <input type="checkbox" aria-label="只看期貨相關" checked={state.relates_futures}
            onChange={e => onPatch({ relates_futures: e.target.checked })} />只看期貨相關
        </label>
        <button type="button" className={styles.clear} onClick={onClear}>清除篩選</button>
      </Popover>
    </div>
  )
}
