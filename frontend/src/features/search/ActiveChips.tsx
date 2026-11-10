import { X } from 'lucide-react'
import { instrumentLabel, reportTypeLabel } from '../../lib/meta'
import type { SearchState } from '../../lib/searchFilters'
import styles from './ActiveChips.module.css'
import { t } from '@/i18n'

interface ChipDef { key: string; label: string; patch: Partial<SearchState> }

interface Props {
  state: SearchState
  onPatch: (p: Partial<SearchState>) => void
}

/** 已套用的進階篩選（點一下移除）。市場不在這裡：市場膠囊本身就看得出選了哪個。 */
export function ActiveChips({ state, onPatch }: Props) {
  const chips: ChipDef[] = []
  if (state.instrument_type) chips.push({ key: 'it', label: t('search.activeChips.assetClass', { instrument_type: instrumentLabel(state.instrument_type) }), patch: { instrument_type: '' } })
  if (state.report_type) chips.push({ key: 'rt', label: t('search.activeChips.type', { report_type: reportTypeLabel(state.report_type) }), patch: { report_type: '' } })
  if (state.relates_stock) chips.push({ key: 'rs', label: t('search.activeChips.stockRelated'), patch: { relates_stock: false } })
  if (state.relates_futures) chips.push({ key: 'rf', label: t('search.activeChips.futuresRelated'), patch: { relates_futures: false } })
  if (chips.length === 0) return null
  return (
    <div className={styles.bar}>
      {chips.map(c => (
        <button key={c.key} type="button" className={styles.chip} aria-label={t('search.activeChips.removeFilter', { label: c.label })} onClick={() => onPatch(c.patch)}>
          {c.label}<X size={12} strokeWidth={2} aria-hidden />
        </button>
      ))}
    </div>
  )
}
