import type { CSSProperties } from 'react'
import { Pressable } from '../../components/primitives/Pressable'
import { MARKET_ORDER, marketColor, marketLabel } from '../../lib/meta'
import styles from './MarketChipBar.module.css'

const ALL = 'ALL'

interface Props {
  value: string
  onChange: (market: string) => void
  /**
   * 省略＝不顯示計數。搜尋且已選定市場時，其他市場的命中數無從得知
   * （後端分面是對已篩選的命中集合計算），此時寧可不顯示也不編造。
   */
  counts?: Record<string, number>
  /** 要列出的市場；省略＝全部。搜尋態只列有命中的市場，零命中的不佔版面。 */
  codes?: readonly string[]
}

export function MarketChipBar({ value, onChange, counts, codes }: Props) {
  const chips = [ALL, ...(codes ?? MARKET_ORDER)]
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
            // 計數在子 span 內（等寬、獨立色），accessible name 會黏成「台股500」；明寫可讀名稱
            aria-label={typeof n === 'number' ? `${label} ${n.toLocaleString()}` : label}
            data-on={active || undefined}
            className={styles.chip}
            // 「全部」是狀態不是市場，故用中性灰而非任何顏色：選取感由填色與字重表達。
            // 不可用 --tf-graphite（近黑，描邊會比整排 chip 都重），也不可用鎏金
            // （#ae7415 與港股 #b26a0b 在同一排裡近到會被讀成另一個市場色）。
            style={{ '--c': code === ALL ? 'var(--tf-text-muted-aa)' : marketColor(code) } as CSSProperties}
            onClick={() => onChange(code)}
          >
            {label}
            {typeof n === 'number' && <span className={styles.n}>{n.toLocaleString()}</span>}
          </Pressable>
        )
      })}
    </div>
  )
}
