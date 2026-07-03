import { MARKET_ORDER, marketLabel } from '../../lib/meta'
import styles from './MarketChipBar.module.css'

const ALL = 'ALL'

interface Props {
  value: string
  onChange: (market: string) => void
  counts?: Record<string, number>
}

export function MarketChipBar({ value, onChange, counts }: Props) {
  const chips = [ALL, ...MARKET_ORDER]
  return (
    <div className={styles.bar} role="tablist" aria-label="市場篩選">
      {chips.map(code => {
        const active = value === code
        const label = code === ALL ? '全部' : marketLabel(code)
        const n = counts?.[code]
        return (
          <button
            key={code}
            type="button"
            role="tab"
            aria-selected={active}
            className={`${styles.chip} ${active ? styles.active : ''}`}
            onClick={() => onChange(code)}
          >
            {label}{typeof n === 'number' ? ` ${n}` : ''}
          </button>
        )
      })}
    </div>
  )
}
