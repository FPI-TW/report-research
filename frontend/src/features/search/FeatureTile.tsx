import { motion, useReducedMotion } from 'motion/react'
import type { CSSProperties } from 'react'
import { marketColor, marketLabel } from '../../lib/meta'
import { springHover } from '../../lib/motionTokens'
import type { ReportRow } from '../../lib/schemas'
import type { SearchMode } from '../../lib/searchFilters'
import styles from './FeatureTile.module.css'

interface Props {
  row: ReportRow
  /** browse：頭條＋「最新」徽章；search：最相關＋相關度分數。 */
  mode: SearchMode
  isLatest: boolean
  className?: string
  onOpen: (id: string, fileName: string) => void
}

/** 頭條磚：最新／最相關的一篇，市場色左緣、大襯線標題。 */
export function FeatureTile({ row, mode, isLatest, className, onOpen }: Props) {
  const reduced = useReducedMotion()
  const market = row.market ?? ''
  const targets = [...(row.stock_targets ?? []), ...(row.futures_targets ?? [])]
  const date = (row.report_date ?? '').slice(0, 10)
  const open = () => onOpen(row.report_id, row.file_name)
  const pct = Math.max(4, Math.min(100, Math.round((row.best_score ?? 0) * 100)))

  const data = [...targets, row.source].filter(Boolean)
  if (mode === 'search' && date) data.push(date.slice(5))

  return (
    <motion.article
      className={`${styles.tile}${className ? ` ${className}` : ''}`}
      style={{ '--c': marketColor(market) } as CSSProperties}
      whileHover={reduced ? undefined : { y: -2, transition: springHover }}
      role="button"
      tabIndex={0}
      aria-label={row.file_name}
      onClick={open}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open() } }}
    >
      <div className={styles.top}>
        <span className={styles.mk}>
          {marketLabel(market)} · {mode === 'search' ? '最相關' : '頭條'}
        </span>
        {mode === 'search' ? (
          <span className={styles.score}>
            <span className={styles.pct}>{pct}%</span>
            <span className={styles.hits}>{row.match_count ?? 0} 段命中</span>
          </span>
        ) : (
          isLatest && <span className={styles.new}>最新</span>
        )}
      </div>

      <div className={styles.title}>{row.file_name}</div>
      {data.length > 0 && <div className={styles.data}>{data.join(' · ')}</div>}
      {row.summary && <p className={styles.sum}>{row.summary}</p>}
      <div className={styles.foot}>
        {mode === 'search' ? '查看全文 ›' : [date, '查看全文 ›'].filter(Boolean).join(' · ')}
      </div>
    </motion.article>
  )
}
