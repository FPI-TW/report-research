import { fmtInt, shortDay } from './analyticsFormat'
import styles from './Analytics.module.css'

/*
 * 手寫 SVG 的小圖（不引入圖表庫）：viewBox 以「每天一格」為座標、preserveAspectRatio="none" 撐滿寬度，
 * 文字（座標軸、圖例）放在 SVG 外的 HTML，縮放時不變形。每一格帶 <title>，滑過看得到當天的值。
 *
 * 沒有換成 console 的 `TrendBars`：範圍最長 365 天，TrendBars 的柱子之間有固定 2px 間距，一年份會撐破版面；
 * 它也不畫缺彙總的日子（這裡用灰柱標出來）與折線。配色與它一致（柱子 l4、今天金色）。
 */

const H = 100

export function BarChart({ title, days, values, today, format = fmtInt }: {
  title: string
  days: string[]
  values: (number | null)[]
  /** 給了而且最後一天就是今天，最後一根用金色（今天還沒過完，數字會再長）。 */
  today?: string
  format?: (n: number | null) => string
}) {
  const max = Math.max(1, ...values.map(v => v ?? 0))
  const n = Math.max(1, values.length)
  const total = values.reduce<number>((a, v) => a + (v ?? 0), 0)
  return (
    <figure className={styles.chart}>
      <figcaption className={styles.chartTitle}>{title}</figcaption>
      <svg className={styles.chartSvg} viewBox={`0 0 ${n} ${H}`} preserveAspectRatio="none" role="img"
        aria-label={`${title}：${days.length} 天，合計 ${format(total)}，單日最高 ${format(max)}`}>
        {values.map((v, i) => {
          const h = v == null ? H : (v / max) * (H - 4)
          return (
            <rect key={days[i] ?? i} x={i + 0.12} width={0.76} y={H - h} height={h}
              className={v == null ? styles.barMissing : styles.bar}
              data-today={(v != null && i === values.length - 1 && days[i] === today) || undefined}>
              <title>{`${days[i]}：${v == null ? '沒有資料' : format(v)}`}</title>
            </rect>
          )
        })}
      </svg>
      <div className={styles.axis}>
        <span>{days.length ? shortDay(days[0]) : ''}</span>
        <span>最高 {format(max)}</span>
        <span>{days.length ? shortDay(days[days.length - 1]) : ''}</span>
      </div>
    </figure>
  )
}

function points(values: (number | null)[], max: number): string[] {
  // 遇到 null 斷線：每段各自一條 polyline。
  const segs: string[] = []
  let cur: string[] = []
  values.forEach((v, i) => {
    if (v == null) {
      if (cur.length) segs.push(cur.join(' '))
      cur = []
      return
    }
    cur.push(`${i + 0.5},${H - (v / max) * (H - 6) - 3}`)
  })
  if (cur.length) segs.push(cur.join(' '))
  return segs
}

export function LineChart({ title, days, a, b, format }: {
  title: string
  days: string[]
  a: { label: string; values: (number | null)[] }
  b: { label: string; values: (number | null)[] }
  format: (n: number | null) => string
}) {
  const max = Math.max(1, ...a.values.map(v => v ?? 0), ...b.values.map(v => v ?? 0))
  const n = Math.max(1, days.length)
  return (
    <figure className={styles.chart}>
      <figcaption className={styles.chartTitle}>{title}</figcaption>
      <svg className={styles.chartSvg} viewBox={`0 0 ${n} ${H}`} preserveAspectRatio="none" role="img"
        aria-label={`${title}：${days.length} 天，最高 ${format(max)}`}>
        {points(b.values, max).map((p, i) => <polyline key={`b${i}`} points={p} className={styles.lineB} />)}
        {points(a.values, max).map((p, i) => <polyline key={`a${i}`} points={p} className={styles.lineA} />)}
      </svg>
      <div className={styles.axis}>
        <span>{days.length ? shortDay(days[0]) : ''}</span>
        <span>最高 {format(max)}</span>
        <span>{days.length ? shortDay(days[days.length - 1]) : ''}</span>
      </div>
      <div className={styles.legend}>
        <span><span className={styles.swatchA} />{a.label}</span>
        <span><span className={styles.swatchB} />{b.label}</span>
      </div>
    </figure>
  )
}
