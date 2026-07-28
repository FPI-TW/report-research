import { MotionLink } from '../../components/primitives/MotionLink'
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
 * 以緊湊清單呈現 —— 原為頁底整條橫幅卡片，太佔閱讀區的垂直空間，故收進側欄。
 *
 * 狀態分工：
 * - 錯誤 → 顯示區塊＋重試（**不可靜默消失**：曾經 500 就整區憑空不見，讀者無從得知）
 * - 載入中 → 不渲染（側欄次要區塊、content-visibility:auto，骨架價值低於它帶來的版面跳動）
 * - 成功且空 → 不渲染（沒有相似研報是常態，不是錯誤）
 * - 成功有結果 → 清單
 */
export function SimilarReports({ items, isLoading, isError, onRetry }: Props) {
  if (isError) {
    return (
      <section className={styles.sim} aria-labelledby="similar-heading">
        <h2 id="similar-heading" className={styles.title}>相似研報</h2>
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
      <h2 id="similar-heading" className={styles.title}>相似研報</h2>
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
              <div className={styles.itemTitle}>{item.file_name}</div>
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
    </section>
  )
}
