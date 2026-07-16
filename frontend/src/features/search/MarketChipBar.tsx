import { Pressable } from '../../components/primitives/Pressable'
import { MARKET_ORDER, marketColor, marketLabel } from '../../lib/meta'
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
    <div className={styles.bar} role="group" aria-label="市場篩選">
      {chips.map(code => {
        const active = value === code
        const label = code === ALL ? '全部' : marketLabel(code)
        const n = counts?.[code]
        return (
          <Pressable
            key={code}
            aria-pressed={active}
            className={`${styles.chip} ${active ? styles.active : ''}`}
            onClick={() => onChange(code)}
          >
            <span className={styles.dot} style={{ background: marketColor(code) }} aria-hidden="true" />
            {label}{typeof n === 'number' ? ` ${n.toLocaleString()}` : ''}
          </Pressable>
        )
      })}
    </div>
  )
}
