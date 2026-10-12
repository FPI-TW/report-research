import c from './TrendBars.module.css'

export interface TrendBar { value: number | null; label: string }

interface Props {
  bars: readonly TrendBar[]
  /** 圖的無障礙名稱（例如「每小時問答」）。 */
  label: string
  /** 數值 → 顯示用的字串（title 提示用）。 */
  format?: (v: number) => string
  /** 最後一根用金色（今日、目前這一小時）。 */
  highlightLast?: boolean
  /** 門檻：畫一條虛線，超過的柱子用 warn 色（主機資源）。 */
  threshold?: number
  /** y 軸上限；不給就取資料（與門檻）的最大值。百分比類的圖給 100。 */
  max?: number
  height?: number
  /** 圖下方的刻度文字（左到右）。 */
  axis?: readonly string[]
}

/** 趨勢長條圖：純 CSS 的柱子，不引入圖表庫。null 的點留空位不畫。 */
export function TrendBars({ bars, label, format = String, highlightLast, threshold, max, height = 64, axis }: Props) {
  const top = max ?? Math.max(1, threshold ?? 0, ...bars.map(b => b.value ?? 0))
  const pct = (v: number) => `${Math.min(100, Math.max(0, (v / top) * 100))}%`
  return (
    <figure className={c.wrap} aria-label={label}>
      <div className={c.plot} style={{ height }} role="img"
        aria-label={`${label}：${bars.filter(b => b.value != null).length} 個資料點`}>
        {threshold != null && <div className={c.threshold} style={{ bottom: pct(threshold) }} title={`門檻 ${format(threshold)}`} />}
        {bars.map((b, i) => (
          <div key={i} className={c.slot} title={b.value == null ? `${b.label}・沒有資料` : `${b.label}・${format(b.value)}`}>
            {b.value != null && (
              <div className={c.bar} style={{ height: pct(b.value) }}
                data-tone={threshold != null && b.value > threshold ? 'warn' : highlightLast && i === bars.length - 1 ? 'gold' : undefined} />
            )}
          </div>
        ))}
      </div>
      {axis && <div className={c.axis}>{axis.map((a, i) => <span key={i}>{a}</span>)}</div>}
    </figure>
  )
}
