import { useReducedMotion } from 'motion/react'
import type { CSSProperties } from 'react'
import { MotionLink } from '../../components/primitives/MotionLink'
import { displayTitle } from '../../lib/displayTitle'
import { highlight } from '../../lib/highlight'
import { marketColor, marketLabel } from '../../lib/meta'
import { revealTransition, revealVariantsFor } from '../../lib/motionTokens'
import type { ReportRow } from '../../lib/schemas'
import { reportHref } from '../report/readingFormat'
import styles from './ResultRow.module.css'

interface Props {
  row: ReportRow
  isLatest: boolean
  terms: string[]
  index?: number
}

/** 搜尋結果列：市場代碼｜標題＋來源摘要｜相關度。依相關度排序，時間不是主軸故不分月。 */
export function ResultRow({ row, isLatest, terms, index = 0 }: Props) {
  const reduced = useReducedMotion()
  const market = row.market ?? ''
  const targets = [...(row.stock_targets ?? []), ...(row.futures_targets ?? [])]
  const date = (row.report_date ?? '').slice(0, 10)
  const meta = [...targets, row.source, date.slice(5)].filter(Boolean).join(' · ')
  const pct = Math.max(4, Math.min(100, Math.round((row.best_score ?? 0) * 100)))
  const snippet = row.passages?.[0]?.content ?? ''

  return (
    // 真連結：cmd+click／中鍵／複製連結；命中的 chunk 帶進 query 供閱讀頁定位。
    <MotionLink
      className={styles.rrow}
      to={reportHref(row.file_hash, row.passages?.[0]?.chunk_index)}
      style={{ '--c': marketColor(market) } as CSSProperties}
      variants={revealVariantsFor('up')}
      initial="hidden"
      animate="visible"
      transition={revealTransition(reduced, index)}
      aria-label={displayTitle(row)}
    >
      <span className={styles.code}>{marketLabel(market)}</span>

      <div className={styles.main}>
        <div className={styles.titleRow}>
          <span className={styles.rtitle}>{displayTitle(row)}</span>
          {isLatest && <span className={styles.new}>最新</span>}
        </div>
        {meta && <div className={styles.meta}>{meta}</div>}
        {snippet && (
          <div className={styles.sum}>
            {highlight(snippet.slice(0, 160), terms)}{snippet.length > 160 ? '…' : ''}
          </div>
        )}
      </div>

      <div className={styles.score}>
        <span className={styles.pct}>{pct}%</span>
        <span className={styles.hits}>{row.match_count ?? 0} 段命中</span>
      </div>
    </MotionLink>
  )
}
