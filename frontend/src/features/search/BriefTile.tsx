import type { CSSProperties } from 'react'
import { Link } from 'react-router'
import { ChevronRight } from 'lucide-react'
import type { BriefEnvelope } from '../../lib/briefSchemas'
import { briefHighlights, isSameLocalDay, shortDate } from './libraryFormat'
import atoms from './atoms.module.css'
import styles from './BriefTile.module.css'

/** 小卡上的重點條數（交接包：4 條）。 */
const HIGHLIGHT_ROWS = 4

interface Props {
  envelope?: BriefEnvelope
  failed: boolean
  onRetry: () => void
  className?: string
  style?: CSSProperties
}

/**
 * 首頁右下：最新一份每日簡報的前幾條重點。
 *
 * 「今日簡報」只在簡報日期就是今天時才這樣叫；不是今天（週末、批次還沒跑）就用頁名「每日簡報」，
 * 不把昨天的簡報說成今天的。市場數只在來源清單完整時才顯示：清單有上限（brief.MAX_REPORTS_IN_PROMPT），
 * 截斷時從清單數市場會少算，寧可不說。
 */
export function BriefTile({ envelope, failed, onRetry, className, style }: Props) {
  const brief = envelope?.status === 'ready' ? envelope.brief ?? null : null
  const today = brief ? isSameLocalDay(brief.brief_date) : false
  const points = brief ? briefHighlights(brief.markdown, HIGHLIGHT_ROWS) : []
  const complete = brief ? brief.reports.length >= brief.report_count : false
  const markets = brief && complete
    ? new Set(brief.reports.map(r => r.market).filter(Boolean)).size
    : null

  return (
    <section
      className={`${atoms.tile} ${styles.tile}${className ? ` ${className}` : ''}`}
      style={style}
      aria-labelledby="library-brief"
    >
      <div className={styles.head}>
        <h2 id="library-brief" className={atoms.h2}>{brief && !today ? '每日簡報' : '今日簡報'}</h2>
        {brief && <span className={`${atoms.num} ${styles.date}`}>{shortDate(brief.brief_date)}</span>}
      </div>

      {failed ? (
        <p className={styles.note}>
          簡報載入失敗
          <button type="button" className={atoms.lk} onClick={onRetry}>重試</button>
        </p>
      ) : !brief ? (
        <p className={styles.note}>今天的簡報尚未產生</p>
      ) : (
        <>
          {points.length > 0 && (
            <ul className={styles.points}>
              {points.map((p, i) => (
                <li key={i} className={styles.point}>
                  <span className={styles.dot} aria-hidden="true" />
                  <span className={styles.pointText}>{p}</span>
                </li>
              ))}
            </ul>
          )}
          <span className={styles.spacer} />
          <div className={styles.stats}>
            <span className={styles.stat}>
              <span className={`${atoms.num} ${styles.statN}`}>{brief.report_count.toLocaleString()}</span>
              <span className={styles.statL}>篇新進研報</span>
            </span>
            {markets !== null && markets > 0 && (
              <span className={styles.stat}>
                <span className={`${atoms.num} ${styles.statN}`}>{markets}</span>
                <span className={styles.statL}>個市場</span>
              </span>
            )}
          </div>
          <Link className={`${atoms.lk} ${styles.more}`} to="/brief">
            閱讀全文<ChevronRight size={14} strokeWidth={1.8} aria-hidden />
          </Link>
        </>
      )}
    </section>
  )
}
