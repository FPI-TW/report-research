import type { CSSProperties } from 'react'
import { ChevronRight } from 'lucide-react'
import { displayTitle } from '../../lib/displayTitle'
import { marketColor, marketLabel } from '../../lib/meta'
import type { ReportRow } from '../../lib/schemas'
import { monthOf, shortDate } from './libraryFormat'
import { PreviewLink } from './PreviewLink'
import atoms from './atoms.module.css'
import styles from './LatestTile.module.css'

interface Props {
  rows: ReportRow[]
  totalReports: number
  onPreview: (row: ReportRow) => void
  onSeeAll: () => void
  className?: string
  style?: CSSProperties
}

/** 首頁下方整列：頭條之後的最新入庫（點一列開預覽）。 */
export function LatestTile({ rows, totalReports, onPreview, onSeeAll, className, style }: Props) {
  const month = monthOf(rows[0]?.report_date)
  return (
    <section
      className={`${atoms.tile} ${styles.tile}${className ? ` ${className}` : ''}`}
      style={style}
      aria-labelledby="library-latest"
    >
      <div className={styles.head}>
        <h2 id="library-latest" className={atoms.h2}>更多最新入庫</h2>
        {month && <span className={`${atoms.num} ${styles.month}`}>{month}</span>}
        <button type="button" className={`${atoms.lk} ${styles.more}`} onClick={onSeeAll}>
          查看全部 <span className={atoms.num}>{totalReports.toLocaleString()}</span> 篇
          <ChevronRight size={14} strokeWidth={1.8} aria-hidden />
        </button>
      </div>
      {rows.map(r => {
        const market = r.market ?? ''
        const title = displayTitle(r)
        return (
          <PreviewLink key={r.report_id} row={r} onPreview={onPreview} className={styles.row} aria-label={title}>
            <span className={styles.market} style={{ color: marketColor(market) }}>{marketLabel(market)}</span>
            <span className={styles.main}>
              <span className={styles.title}>{title}</span>
              {r.source && <span className={styles.broker}>{r.source}</span>}
            </span>
            <span className={`${atoms.num} ${styles.date}`}>{shortDate(r.report_date)}</span>
          </PreviewLink>
        )
      })}
    </section>
  )
}
