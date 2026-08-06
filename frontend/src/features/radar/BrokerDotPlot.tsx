import { useId, type ReactNode } from 'react'
import { axisPosition, FRESHNESS_LABEL, type DotSeries } from './consensusSeries'
import { fmtDateOrNA } from './radarFormat'
import styles from './BrokerDotPlot.module.css'

interface Props {
  title: string
  /** 軸標題（含幣別／單位）。缺資料時不渲染軸，這個字串也就不出現。 */
  axisLabel: string
  series: DotSeries
  selectedKey: string | null
  onSelect: (key: string) => void
  /** 標題右側的口徑選擇器（EPS 用），目標價圖不傳。 */
  control?: ReactNode
  emptyText: string
  /** 測試與詳情面板用的前綴，例如 `target` / `eps`。 */
  testPrefix: string
}

/**
 * 水平點圖：一列一家券商，圓點落在共用的數值軸上。
 *
 * **為什麼是 DOM 絕對定位而不是 SVG**：每個標記都必須是真的 `<button>`（可 Tab、可
 * Enter、有 `aria-pressed`），而 SVG 裡的可聚焦元素在各家輔助技術上的支援參差不齊；
 * 這個定位模式（`.track { position: relative }` ＋ `left: N%` ＋ `translateX(-50%)`）
 * 本來就是 `ConsensusSnapshot` 中位指針在用的那一套，不是新發明。
 *
 * **新鮮度不靠顏色**：90 天內是實心、超過 90 天是空心（形狀），旁邊另有文字圖例，
 * 每個點的 `aria-label` 與 `title` 也把「90 天內／超過 90 天」講出來。
 *
 * **不做垂直錯位的原因**：一家券商一列，數值再接近也不會疊在一起——錯位是「多點共用
 * 一條軸」才需要的補救，而那種畫法會讓「這個點是誰」必須靠 hover 才知道。
 */
export function BrokerDotPlot({
  title, axisLabel, series, selectedKey, onSelect, control, emptyText, testPrefix,
}: Props) {
  const uid = useId()
  const titleId = `${uid}-title`
  const { points, excluded, scale, missing } = series

  const hasUnknown = points.some(point => point.freshness === 'unknown')
  const counts = [
    `共 ${points.length} 家有資料`,
    excluded.length ? `${excluded.length} 家不同口徑` : '',
    missing ? `${missing} 家未提供` : '',
  ].filter(Boolean).join(' · ')

  return (
    <section className={styles.chart} aria-labelledby={titleId}>
      <div className={styles.head}>
        <h3 className={styles.title} id={titleId}>{title}</h3>
        {control}
        {/* 圖例是文字＋形狀，不是色塊：新鮮度的唯一視覺編碼是實心／空心／虛線。
            「報告日期不明」**只在真的出現時**才進圖例——先前它與「超過 90 天」共用空心，
            等於替一份沒有日期的研報宣稱它超過 90 天，而那是資料裡沒有的事。 */}
        <ul className={styles.legend}>
          <li><i className={styles.dotSolid} aria-hidden="true" />{FRESHNESS_LABEL.recent}</li>
          <li><i className={styles.dotHollow} aria-hidden="true" />{FRESHNESS_LABEL.stale}</li>
          {hasUnknown ? (
            <li><i className={styles.dotUnknown} aria-hidden="true" />{FRESHNESS_LABEL.unknown}</li>
          ) : null}
        </ul>
      </div>
      <p className={styles.sub}>每個點代表一家券商 · {counts}</p>

      {points.length && scale ? (
        <div className={styles.rows} role="group" aria-label={`${title}，${counts}`}>
          {points.map(point => {
            const pct = axisPosition(point.value, scale)
            const selected = point.key === selectedKey
            const freshLabel = FRESHNESS_LABEL[point.freshness]
            const description
              = `${point.broker}，${point.valueLabel}，報告日期 ${fmtDateOrNA(point.reportDate)}，${freshLabel}`
            return (
              <div className={styles.row} key={point.key}>
                <span className={styles.name} title={point.broker}>{point.broker}</span>
                <div className={styles.track}>
                  {scale.ticks.map(tick => (
                    <span
                      key={tick}
                      className={styles.gridline}
                      style={{ left: `${axisPosition(tick, scale)}%` }}
                      aria-hidden="true"
                    />
                  ))}
                  {/* title 掛在**外層 span** 而不是按鈕上：按鈕已有 aria-label 當可及名稱，
                      再掛 title 會讓它變成可及**描述**，讀屏於是把同一句話唸兩次。
                      掛在祖先元素上則不參與按鈕的名稱／描述計算，滑鼠仍然拿得到提示。 */}
                  <span
                    className={styles.dotWrap}
                    style={{ left: `${pct}%` }}
                    title={description}
                    data-testid={`${testPrefix}-pos-${point.key}`}
                  >
                    <button
                      type="button"
                      className={`${styles.dot} ${styles[point.freshness]} ${selected ? styles.dotOn : ''}`}
                      aria-pressed={selected}
                      aria-label={description}
                      data-testid={`${testPrefix}-dot-${point.key}`}
                      onClick={() => onSelect(point.key)}
                    >
                      <span className={styles.mark} aria-hidden="true" />
                    </button>
                  </span>
                  {/* 值標籤跟著點走；靠右端時翻到左邊，否則會被軌道裁掉。 */}
                  <span
                    className={`${styles.value} ${pct > 82 ? styles.valueFlip : ''}`}
                    style={{ left: `${pct}%` }}
                    aria-hidden="true"
                  >
                    {point.valueLabel}
                  </span>
                </div>
              </div>
            )
          })}

          {excluded.map(row => (
            <div className={`${styles.row} ${styles.rowMuted}`} key={`x-${row.key}`}>
              <span className={styles.name} title={row.broker}>{row.broker}</span>
              <div className={styles.track}>
                <span className={styles.exclNote} data-testid={`${testPrefix}-excluded-${row.key}`}>
                  {row.note}
                </span>
              </div>
            </div>
          ))}

          <div className={styles.axisRow}>
            <span />
            <div className={styles.axis}>
              {scale.ticks.map(tick => (
                <span
                  key={tick}
                  className={styles.tick}
                  style={{ left: `${axisPosition(tick, scale)}%` }}
                >
                  {tick.toLocaleString('zh-TW')}
                </span>
              ))}
            </div>
          </div>
          <p className={styles.axisLabel}>{axisLabel}</p>
        </div>
      ) : (
        <div className={styles.empty}>
          <p>{emptyText}</p>
          {excluded.length ? (
            <ul className={styles.exclList}>
              {excluded.map(row => (
                <li key={row.key} data-testid={`${testPrefix}-excluded-${row.key}`}>
                  {row.broker} · {row.note}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      )}
    </section>
  )
}
