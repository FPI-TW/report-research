import type { DimLabel, ThesisDimension } from '../../lib/radarSchemas'
import styles from './ThesisCompass.module.css'

const TONE: Record<DimLabel, string> = {
  strengthen: styles.strengthen,
  weaken: styles.weaken,
  diverging: styles.diverging,
  stable: styles.stable,
  insufficient: styles.insufficient,
}

/**
 * 計數句：零值一律不印。
 *
 * 舊版恆印「0 家轉強 · 0 家轉弱 · 2 家可比」——三個數字裡有兩個是零，而零轉強與零轉弱
 * 講的是同一件事（沒人動）。說「2 家可比，皆未變動」一句就夠，還比較好讀。
 */
function countsText(up: number, down: number, n: number): string {
  if (n === 0) return '沒有可比較的前次觀點'
  const moved = [
    up > 0 ? `${up} 家轉強` : '',
    down > 0 ? `${down} 家轉弱` : '',
  ].filter(Boolean)
  if (!moved.length) return `${n} 家可比，皆未變動`
  return `${moved.join(' · ')} · ${n} 家可比`
}

interface Props {
  thesis: ThesisDimension[]
}

/**
 * 四向觀點：一台儀器上的四個方位，不是四台各自為政的儀器。
 *
 * 改版前是四張獨立卡片，各自帶邊框與陰影——十幾個字的內容付了四圈框。收成單一面板
 * ＋髮絲線分欄之後，讀者掃的是四欄之間的差異，而不是四個一模一樣的方框。
 */
export function ThesisCompass({ thesis }: Props) {
  return (
    <section className={styles.panel} aria-label="四向觀點">
      <div className={styles.grid}>
        {thesis.map(dim => <Bearing key={dim.dimension} dim={dim} />)}
      </div>
      {/* 口徑只講一次，不在四欄各講一遍。摘述的來源與可信度必須寫明：它與上面的
          數字不同源——數字是 Python 數出來的，這句話是模型改寫的。 */}
      <p className={styles.caliber}>
        刻度為窗期內每家券商與其前次研報的比較；摘述由模型自最新一份研報改寫，非原文引述。
      </p>
    </section>
  )
}

function Bearing({ dim }: { dim: ThesisDimension }) {
  const n = dim.brokers_comparable
  const up = dim.brokers_strengthen
  const down = dim.brokers_weaken
  const flat = Math.max(0, n - up - down)
  // 轉強 → 未變動 → 轉弱：同色相鄰才聚得成一叢，讀者掃的是那叢的大小與顏色。
  const marks: Array<'up' | 'flat' | 'down'> = [
    ...Array.from({ length: up }, () => 'up' as const),
    ...Array.from({ length: flat }, () => 'flat' as const),
    ...Array.from({ length: down }, () => 'down' as const),
  ]

  return (
    <article className={styles.bearing}>
      {/* 維度名是「這一欄在講什麼」，不是結論——所以降成 eyebrow。改版前它是 16px/600，
          比真正的答案（12px 的小膠囊）還大，階層整個是反的。 */}
      <span className={styles.axis}>{dim.dimension_display}</span>
      <span className={`${styles.verdict} ${TONE[dim.label]}`}>{dim.label_display}</span>

      {/* 一家券商一個刻度。連續長條在 n=1~3 時是在謊報精度（1/2 家與 7/14 家畫出來
          一模一樣），而全數未變動時它會整條填滿成灰塊、看起來像「進度 100%」——那正是
          改版前最醒目也最誤導的元素。刻度數得出來，而且 n=0 時是看得見的空。 */}
      {marks.length > 0 ? (
        <span className={styles.tally} aria-hidden="true">
          {marks.map((m, i) => <i key={i} className={styles[m]} />)}
        </span>
      ) : (
        <span className={styles.tallyEmpty} aria-hidden="true">—</span>
      )}

      <span className={styles.counts}>{countsText(up, down, n)}</span>

      {/* 髮絲線標的是來源的交界：線以上是算出來的，線以下是模型寫的。 */}
      {dim.sample_summary ? (
        <p className={styles.summary} title={dim.sample_summary}>{dim.sample_summary}</p>
      ) : null}
    </article>
  )
}
