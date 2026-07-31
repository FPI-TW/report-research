import { useState } from 'react'
import { Icon } from '../../components/primitives/Icon'
import { MotionLink } from '../../components/primitives/MotionLink'
import { displayTitle } from '../../lib/displayTitle'
import { marketLabel, marketTint } from '../../lib/meta'
import { springHover } from '../../lib/motionTokens'
import type { SimilarReport } from '../../lib/readingSchemas'
import { matchedText, reportHref, similarMeta } from './readingFormat'
import styles from './SimilarReports.module.css'

interface Props {
  items: SimilarReport[]
  isLoading: boolean
  isError: boolean
  onRetry: () => void
}

/**
 * 相似研報。左側智慧欄的第 4 個區塊（摘要 → 重點摘錄 → 觀點 → 相似研報），
 * 位於卡片頁腳、**預設收合成一行**：側欄的主體是摘要與重點摘錄，相似研報是
 * 「離開本篇」的入口，常駐展開會把主體往上推走。
 *
 * 狀態分工：
 * - 錯誤 → 顯示區塊＋重試，且**不收合**（**不可靜默消失**：曾經 500 就整區憑空不見，
 *   讀者無從得知；收在摺疊裡按不到重試，等於同一個病復發）
 * - 載入中 → 不渲染（側欄次要區塊、content-visibility:auto，骨架價值低於它帶來的版面跳動）
 * - 成功且空 → 不渲染（沒有相似研報是常態，不是錯誤）
 * - 成功有結果 → 收合列（標題＋篇數＋人字），展開才掛清單
 */
export function SimilarReports({ items, isLoading, isError, onRetry }: Props) {
  const [open, setOpen] = useState(false)

  if (isError) {
    return (
      <section className={`${styles.sim} ${styles.simErr}`} aria-labelledby="similar-heading">
        <h2 id="similar-heading" className={styles.errHead}>相似研報</h2>
        <div className={styles.failed} role="status">
          <span>相似研報載入失敗。</span>
          <button type="button" className={styles.retry} onClick={onRetry}>重試</button>
        </div>
      </section>
    )
  }

  if (isLoading || items.length === 0) return null

  return (
    <section className={styles.sim} aria-labelledby="similar-heading">
      {/* 開合鈕包在 h2 之內：標題語意留給 h2、操作語意留給 button（ARIA disclosure 慣例）。
          h2 的直接子節點只有 button，故 getByText('相似研報') 仍唯一命中內層的 span。 */}
      <h2 id="similar-heading" className={styles.head}>
        <button
          type="button"
          className={styles.row}
          aria-expanded={open}
          aria-controls="similar-body"
          onClick={() => setOpen(o => !o)}
        >
          <span className={styles.title}>相似研報</span>
          <span className={styles.count}>{items.length}</span>
          <span className={open ? `${styles.chev} ${styles.chevOpen}` : styles.chev} aria-hidden="true">
            <Icon name="chevronDown" size={13} strokeWidth={2} />
          </span>
        </button>
      </h2>
      {/* 收合時整段不進 DOM：清單列是連結，留在 DOM 內即使視覺隱藏仍會進 Tab 序。 */}
      {open && (
        <div id="similar-body" className={styles.body}>
          {/* 說法要對得上演算法：probe 是沿全文「均勻取樣」的段落，不是把全文「切成」N 段。 */}
          <p className={styles.note}>沿全文均勻取樣 {items[0]?.total_probes ?? 0} 個段落比對，顯示重疊最高者</p>
          <div className={styles.list}>
            {items.map(item => {
              const meta = similarMeta(item)
              return (
                <MotionLink
                  key={item.file_hash}
                  className={styles.item}
                  to={reportHref(item.file_hash)}
                  whileHover={{ x: 2 }}
                  transition={springHover}
                >
                  <div className={styles.itemTitle}>{displayTitle(item)}</div>
                  <div className={styles.meta}>
                    {item.market && (
                      <span className={styles.mkt} style={marketTint(item.market)}>
                        {marketLabel(item.market)}
                      </span>
                    )}
                    {meta && <span>{meta}</span>}
                    <span className={styles.match}>{matchedText(item.matched_probes, item.total_probes)}</span>
                  </div>
                </MotionLink>
              )
            })}
          </div>
        </div>
      )}
    </section>
  )
}
