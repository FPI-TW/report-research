import { marketLabel, instrumentLabel, reportTypeLabel } from '../../lib/meta'
import type { SearchState } from '../../lib/searchFilters'
import styles from './ActiveChips.module.css'

interface ChipDef { label: string; patch: Partial<SearchState> }

interface Props {
  state: SearchState
  onPatch: (p: Partial<SearchState>) => void
}

export function ActiveChips({ state, onPatch }: Props) {
  const chips: ChipDef[] = []
  if (state.market !== 'ALL') chips.push({ label: `市場：${marketLabel(state.market)}`, patch: { market: 'ALL' } })
  if (state.instrument_type) chips.push({ label: `商品：${instrumentLabel(state.instrument_type)}`, patch: { instrument_type: '' } })
  if (state.report_type) chips.push({ label: `類型：${reportTypeLabel(state.report_type)}`, patch: { report_type: '' } })
  if (state.relates_stock) chips.push({ label: '個股相關', patch: { relates_stock: false } })
  if (state.relates_futures) chips.push({ label: '期貨相關', patch: { relates_futures: false } })
  if (chips.length === 0) return null
  return (
    <div className={styles.bar}>
      {chips.map(c => {
        // 以 patch 欄位名作穩定 key（每 chip 對應唯一篩選欄位），
        // 讓既有 chip 不因序位變動而重掛、只有新加入者才觸發 chipIn 彈入動畫
        const key = Object.keys(c.patch)[0]
        return (
          <button key={key} type="button" className={styles.chip} onClick={() => onPatch(c.patch)}>
            {c.label}<span className={styles.x}>×</span>
          </button>
        )
      })}
    </div>
  )
}
