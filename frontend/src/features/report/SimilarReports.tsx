import { Link } from 'react-router'
import { displayTitle } from '../../lib/displayTitle'
import { marketColor, marketLabel } from '../../lib/meta'
import type { SimilarReport } from '../../lib/readingSchemas'
import type { ReportLinkState } from './backNav'
import { fmtDate, reportHref } from './readingFormat'
import panel from './panel.module.css'
import styles from './SimilarReports.module.css'

interface Props {
  items: SimilarReport[]
  isLoading: boolean
  isError: boolean
  onRetry: () => void
  /** 點進下一篇時帶的 location.state：返回鈕要一次退回來源頁，見 backNav.ts */
  linkState?: ReportLinkState
}

/**
 * 相似研報：研報面板裡直接列出（交接包「閱讀頁」第 6 點），不再收合。
 *
 * 狀態分工：
 * - 錯誤 → 顯示區塊＋重試（**不可靜默消失**：曾經 500 就整區憑空不見，讀者無從得知）
 * - 載入中 → 不渲染（面板次要區塊，骨架價值低於它帶來的版面跳動）
 * - 成功且空 → 不渲染（沒有相似研報是常態，不是錯誤）
 */
export function SimilarReports({ items, isLoading, isError, onRetry, linkState }: Props) {
  if (isError) {
    return (
      <section className={panel.sec} aria-labelledby="similar-heading">
        <h2 id="similar-heading" className={panel.h}>相似研報</h2>
        <div className={styles.failed} role="status">
          <span>相似研報載入失敗。</span>
          <button type="button" className={styles.retry} onClick={onRetry}>重試</button>
        </div>
      </section>
    )
  }

  if (isLoading || items.length === 0) return null

  return (
    <section className={panel.sec} aria-labelledby="similar-heading">
      <h2 id="similar-heading" className={panel.h}>相似研報</h2>
      <div className={styles.list}>
        {items.map(item => {
          const broker = item.source_display || item.source || null
          const date = fmtDate(item.report_date)
          return (
            <Link key={item.file_hash} className={styles.row} to={reportHref(item.file_hash)} state={linkState}>
              <span className={styles.mkt} style={{ color: item.market ? marketColor(item.market) : undefined }}>
                {item.market ? marketLabel(item.market) : ''}
              </span>
              <span className={styles.body}>
                <span className={styles.title}>{displayTitle(item)}</span>
                {(broker || date) && (
                  <span className={styles.meta}>
                    {broker && <span>{broker}</span>}
                    {date && <span className={styles.num}>{date}</span>}
                  </span>
                )}
              </span>
            </Link>
          )
        })}
      </div>
    </section>
  )
}
