import { useEnabledMarkets } from '../../lib/marketsConfig'
import { marketLabel } from '../../lib/meta'
import styles from './MarketChipBar.module.css'
import { t } from '@/i18n'

const ALL = 'ALL'

interface Props {
  value: string
  onChange: (market: string) => void
  /**
   * 省略＝不顯示計數。搜尋且已選定市場時，其他市場的命中數無從得知
   * （後端分面是對已篩選的命中集合計算），此時寧可不顯示也不編造。
   */
  counts?: Record<string, number>
  /** 要列出的市場；省略＝profile 啟用的全部市場。搜尋態只列有命中的市場，零命中的不佔版面。 */
  codes?: readonly string[]
}

/** 市場篩選膠囊（32px，含篇數）。選取中的是石墨實心，不用市場色——選取是狀態，不是市場。 */
export function MarketChipBar({ value, onChange, counts, codes }: Props) {
  const enabled = useEnabledMarkets()
  const chips = [ALL, ...(codes ?? enabled)]
  return (
    <div className={styles.bar} role="group" aria-label={t('search.marketChipBar.marketFilter')}>
      {chips.map(code => {
        const active = value === code
        const label = code === ALL ? t('search.marketChipBar.all') : marketLabel(code)
        const n = counts?.[code]
        return (
          <button
            key={code}
            type="button"
            aria-pressed={active}
            // 計數在子 span 內，accessible name 會黏成「台股500」；明寫可讀名稱
            aria-label={typeof n === 'number' ? `${label} ${n.toLocaleString()}` : label}
            data-on={active || undefined}
            className={styles.chip}
            onClick={() => onChange(code)}
          >
            {label}
            {typeof n === 'number' && <span className={styles.n}>{n.toLocaleString()}</span>}
          </button>
        )
      })}
    </div>
  )
}
