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
import { t } from '@/i18n'

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
      <span>{t('search.headlineTile.consensus')}</span>
      <span className={`${atoms.badge} ${atoms[median]}`}>{RATING_LABEL[median]}</span>
      <span><span className={`${atoms.num} ${styles.strong}`}>{brokers}</span>{' '}{t('search.headlineTile.brokers')}</span>
      {rating.upgrades > 0 && <span className={styles.up}>{t('search.headlineTile.up')}{' '}<span className={atoms.num}>{rating.upgrades}</span></span>}
      {rating.downgrades > 0 && <span className={styles.down}>{t('search.headlineTile.down')}{' '}<span className={atoms.num}>{rating.downgrades}</span></span>}
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
        <span className={styles.kicker}>{t('search.headlineTile.headline')}</span>
        {isLatest && <span className={styles.latest}>{t('search.headlineTile.latest')}</span>}
      </span>
      <span className={styles.title}>{title}</span>
      {targets.length > 0 && (
        <span className={styles.targets}>
          {targets.map(item => <span key={item} className={atoms.codeChip}>{item}</span>)}
        </span>
      )}
      {row.summary && <span className={styles.summary}>{row.summary}</span>}

      {takeaways.length > 0 && (
        <span className={styles.points}>
          <span className={styles.pointsLabel}>{t('search.headlineTile.keyTakeaway')}</span>
          {takeaways.map((item, i) => (
            <span key={item.ordinal} className={styles.point}>
              <span className={`${atoms.num} ${styles.pointN}`}>{i + 1}</span>
              <span className={styles.pointBody}>
                <span className={styles.claim}>{item.claim}</span>
                {/* 逐字引文不轉繁簡（它是 PDF 搜尋的關鍵字），只加引號 */}
                {item.quote && <span className={styles.quote}>{t('search.headlineTile.text', { quote: item.quote })}</span>}
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
        <span className={styles.cta}>{t('search.headlineTile.viewFullText')}<ArrowRight size={14} strokeWidth={2} aria-hidden /></span>
      </span>
    </Link>
  )
}
