import type { CSSProperties } from 'react'
import { displayTitle } from '../../lib/displayTitle'
import { highlight } from '../../lib/highlight'
import { marketColor, marketLabel } from '../../lib/meta'
import type { ReportRow } from '../../lib/schemas'
import { fullDate } from './libraryFormat'
import { PreviewLink } from './PreviewLink'
import atoms from './atoms.module.css'
import styles from './ResultRow.module.css'

interface Props {
  row: ReportRow
  /** 搜尋時第一篇標「最相關」。 */
  isTop: boolean
  terms: string[]
  index: number
  onPreview: (row: ReportRow) => void
}

/** 結果清單的一列：市場、券商、日期、（最相關），標題，兩行摘要。點一下開預覽。 */
export function ResultRow({ row, isTop, terms, index, onPreview }: Props) {
  const market = row.market ?? ''
  const title = displayTitle(row)
  // 摘要缺值時退回命中段落，至少讓讀者看得到這篇為什麼被找出來
  const text = row.summary || row.passages?.[0]?.content || ''
  return (
    <PreviewLink
      row={row}
      onPreview={onPreview}
      className={styles.row}
      style={{ '--i': Math.min(index, 6) } as CSSProperties}
      aria-label={title}
    >
      <span className={styles.meta}>
        <span className={styles.market} style={{ color: marketColor(market) }}>{marketLabel(market)}</span>
        {row.source && <span className={styles.broker}>{row.source}</span>}
        {row.report_date && <span className={`${atoms.num} ${styles.date}`}>{fullDate(row.report_date)}</span>}
        {isTop && <span className={styles.top}>最相關</span>}
      </span>
      <span className={styles.title}>{title}</span>
      {text && <span className={styles.summary}>{highlight(text, terms)}</span>}
    </PreviewLink>
  )
}
