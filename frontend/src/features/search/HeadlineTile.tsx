import type { CSSProperties } from 'react'
import { Link } from 'react-router'
import { ArrowRight } from 'lucide-react'
import { displayTitle } from '../../lib/displayTitle'
import { marketColor, marketLabel } from '../../lib/meta'
import type { RadarOverview } from '../../lib/radarSchemas'
import type { ReadingDoc } from '../../lib/readingSchemas'
import type { ReportRow } from '../../lib/schemas'
import { reportHref } from '../report/readingFormat'
import { RATING_LABEL, fullDate } from './libraryFormat'
import atoms from './atoms.module.css'
import styles from './HeadlineTile.module.css'

/** 頭條重點摘錄的條數（交接包：3 條）。 */
const TAKEAWAY_ROWS = 3
/** 標的代號最多列幾個；多於此數的研報多半是產業報告，全列出來只是一排噪音。 */
const TARGET_CHIPS = 6

interface Props {
  row: ReportRow
  isLatest: boolean
  doc?: ReadingDoc
  consensus?: RadarOverview
  className?: string
  style?: CSSProperties
}

function ConsensusRow({ consensus }: { consensus: RadarOverview }) {
  const rating = consensus.rating
  const median = rating?.median_rating
  if (!rating || rating.total_rated === 0 || !median || median === 'unknown') return null
  const brokers = consensus.coverage.brokers_in_consensus
  return (
    <span className={styles.consensus}>
      <span>券商共識</span>
      <span className={`${atoms.badge} ${atoms[median]}`}>{RATING_LABEL[median]}</span>
      <span><span className={`${atoms.num} ${styles.strong}`}>{brokers}</span> 家券商</span>
      {rating.upgrades > 0 && <span className={styles.up}>調升 <span className={atoms.num}>{rating.upgrades}</span></span>}
      {rating.downgrades > 0 && <span className={styles.down}>調降 <span className={atoms.num}>{rating.downgrades}</span></span>}
    </span>
  )
}

/**
 * 頭條大卡：最新入庫的第一篇。整張是連到閱讀頁的真連結（「查看全文 →」只是標示），
 * 所以卡內不再放其他可點的東西——巢狀互動元素對鍵盤與讀屏都不成立。
 */
export function HeadlineTile({ row, isLatest, doc, consensus, className, style }: Props) {
  const market = row.market ?? ''
  const title = displayTitle(row)
  const targets = [...(row.stock_targets ?? []), ...(row.futures_targets ?? [])].slice(0, TARGET_CHIPS)
  const takeaways = (doc?.takeaways ?? []).slice(0, TAKEAWAY_ROWS)

  return (
    <Link
      to={reportHref(row.file_hash)}
      className={`${atoms.tile} ${styles.tile}${className ? ` ${className}` : ''}`}
      style={style}
      aria-label={title}
    >
      <span className={styles.top}>
        <span className={styles.market} style={{ color: marketColor(market) }}>{marketLabel(market)}</span>
        <span className={styles.kicker}>頭條</span>
        {isLatest && <span className={styles.latest}>最新</span>}
      </span>
      <span className={styles.title}>{title}</span>
      {targets.length > 0 && (
        <span className={styles.targets}>
          {targets.map(t => <span key={t} className={atoms.codeChip}>{t}</span>)}
        </span>
      )}
      {row.summary && <span className={styles.summary}>{row.summary}</span>}

      {takeaways.length > 0 && (
        <span className={styles.points}>
          <span className={styles.pointsLabel}>重點摘錄</span>
          {takeaways.map((t, i) => (
            <span key={t.ordinal} className={styles.point}>
              <span className={`${atoms.num} ${styles.pointN}`}>{i + 1}</span>
              <span className={styles.pointBody}>
                <span className={styles.claim}>{t.claim}</span>
                {/* 逐字引文不轉繁簡（它是 PDF 搜尋的關鍵字），只加引號 */}
                {t.quote && <span className={styles.quote}>「{t.quote}」</span>}
              </span>
            </span>
          ))}
        </span>
      )}

      <span className={styles.spacer} />
      {consensus && <ConsensusRow consensus={consensus} />}
      <span className={styles.foot}>
        {row.source && <span>{row.source}</span>}
        {row.report_date && <span className={atoms.num}>{fullDate(row.report_date)}</span>}
        <span className={styles.cta}>查看全文<ArrowRight size={14} strokeWidth={2} aria-hidden /></span>
      </span>
    </Link>
  )
}
