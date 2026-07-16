import { Pressable } from '../../components/primitives/Pressable'
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
      {chips.map((c, i) => (
        <Pressable key={i} className={styles.chip} onClick={() => onPatch(c.patch)}>
          {c.label}<span className={styles.x}>×</span>
        </Pressable>
      ))}
    </div>
  )
}
