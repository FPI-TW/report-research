import { useState } from 'react'
import { Popover } from '../../components/primitives/Popover'
import { Icon } from '../../components/primitives/Icon'
import { activeAdvancedCount, type SearchState } from '../../lib/searchFilters'
import styles from './MoreFiltersPopover.module.css'

interface Props {
  state: SearchState
  instrumentOptions: string[]
  reportTypeOptions: string[]
  /** 按「套用」時提交進階篩選草稿（一次 patch，觸發一次重抓）。 */
  onApply: (patch: Partial<SearchState>) => void
}

/** 彈出層內部編輯用的進階篩選草稿；「套用」前不動父層狀態。 */
interface Draft {
  instrument_type: string
  report_type: string
  relates_stock: boolean
  relates_futures: boolean
}

const EMPTY: Draft = {
  instrument_type: '', report_type: '', relates_stock: false, relates_futures: false,
}

function pick(s: SearchState): Draft {
  return {
    instrument_type: s.instrument_type,
    report_type: s.report_type,
    relates_stock: s.relates_stock,
    relates_futures: s.relates_futures,
  }
}

// 對齊 Claude Design（.dc.html 更多篩選彈出層）：pill 切換 + 清除條件/套用（apply-on-button）。
// 註：.dc.html 的「個股 · 標的」為標的代碼/名稱文字框，但後端無「標的值」篩選端點，
// 依 Phase 1 §0 既定決策改為 個股/期貨 兩顆布林 pill（section 標題相應為「個股 · 期貨」）。
export function MoreFiltersPopover({ state, instrumentOptions, reportTypeOptions, onApply }: Props) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<Draft>(() => pick(state))
  // 每次開啟時以目前「已套用」狀態重新種入草稿：於 render 期間調整 state（React 官方
  // 「adjust state during render」模式），避免在 effect 內 setState。
  const [prevOpen, setPrevOpen] = useState(open)
  if (open !== prevOpen) {
    setPrevOpen(open)
    if (open) setDraft(pick(state))
  }
  const count = activeAdvancedCount(state)

  const pickOne = (field: 'instrument_type' | 'report_type', value: string) =>
    setDraft(d => ({ ...d, [field]: d[field] === value ? '' : value }))
  const toggle = (field: 'relates_stock' | 'relates_futures') =>
    setDraft(d => ({ ...d, [field]: !d[field] }))
  const apply = () => { onApply(draft); setOpen(false) }

  const pillClass = (active: boolean) => (active ? styles.pillActive : styles.pill)

  return (
    <div className={styles.wrap}>
      <button type="button" className={styles.trigger} onClick={() => setOpen(o => !o)}
        aria-haspopup="dialog" aria-expanded={open}>
        <Icon name="filter" size={18} />
        更多篩選
        {count > 0 && <span className={styles.badge}>{count}</span>}
        <Icon name="chevronDown" size={15} />
      </button>
      <Popover open={open} onClose={() => setOpen(false)} className={styles.panel}
        role="dialog" ariaLabel="更多篩選">
        <div className={styles.sectionLabel}>商品類型</div>
        <div className={styles.pillRow}>
          {instrumentOptions.map(o => (
            <button key={o} type="button" className={pillClass(draft.instrument_type === o)}
              aria-pressed={draft.instrument_type === o}
              onClick={() => pickOne('instrument_type', o)}>{o}</button>
          ))}
        </div>

        <div className={styles.sectionLabel}>報告類型</div>
        <div className={styles.pillRow}>
          {reportTypeOptions.map(o => (
            <button key={o} type="button" className={pillClass(draft.report_type === o)}
              aria-pressed={draft.report_type === o}
              onClick={() => pickOne('report_type', o)}>{o}</button>
          ))}
        </div>

        <div className={styles.sectionLabel}>個股 · 期貨</div>
        <div className={styles.pillRow}>
          <button type="button" className={pillClass(draft.relates_stock)}
            aria-pressed={draft.relates_stock}
            onClick={() => toggle('relates_stock')}>個股</button>
          <button type="button" className={pillClass(draft.relates_futures)}
            aria-pressed={draft.relates_futures}
            onClick={() => toggle('relates_futures')}>期貨</button>
        </div>

        <div className={styles.footer}>
          <button type="button" className={styles.clearBtn}
            onClick={() => setDraft(EMPTY)}>清除條件</button>
          <button type="button" className={styles.applyBtn} onClick={apply}>套用</button>
        </div>
      </Popover>
    </div>
  )
}
