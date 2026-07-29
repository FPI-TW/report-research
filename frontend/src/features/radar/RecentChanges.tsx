import { Pressable } from '../../components/primitives/Pressable'
import { displayTitle } from '../../lib/displayTitle'
import type { EventCard as EventCardT } from '../../lib/radarSchemas'
import { DirectionTag } from './DirectionTag'
import { fmtDate } from './radarFormat'
import styles from './RecentChanges.module.css'

interface Props {
  events: EventCardT[]
  total: number
  onOpenReport: (reportId: string, fileName?: string | null) => void
  showAll?: boolean
  onToggleAll?: () => void
  hasMore?: boolean
  remaining?: number
  isLoadingMore?: boolean
  loadError?: boolean
  onLoadMore?: () => void
  onRetryLoad?: () => void
}

const PREVIEW = 3

export function RecentChanges({
  events,
  onOpenReport,
  showAll,
  hasMore,
  remaining = 0,
  isLoadingMore,
  loadError,
  onLoadMore,
  onRetryLoad,
}: Props) {
  const visible = showAll ? events : events.slice(0, PREVIEW)

  if (!events.length) {
    return <div className={styles.empty}>此窗期尚無實質觀點變化事件。</div>
  }

  return (
    <div className={styles.feed} id="radar-recent-events">
      {visible.map((ev, i) => (
        <article
          key={`${ev.report_link.report_id}-${ev.report_date}-${i}`}
          className={styles.event}
        >
          <div className={styles.card}>
            <div className={styles.meta}>
              <span className={styles.date}>{fmtDate(ev.report_date)}</span>
              <span className={styles.broker}>{ev.broker_display || ev.broker || '未知券商'}</span>
            </div>
            <p className={styles.headline}>{ev.headline}</p>
            {ev.changes.length > 0 ? (
              <div className={styles.tags}>
                {ev.changes.slice(0, 3).map((c, j) => (
                  <DirectionTag
                    key={`${c.field}-${j}`}
                    direction={c.direction}
                    label={c.label}
                    pct={c.pct_change}
                  />
                ))}
              </div>
            ) : null}
            <div className={styles.foot}>
              {ev.evidence[0] ? <p className={styles.cite}>「{ev.evidence[0]}」</p> : <span />}
              <Pressable
                type="button"
                tapScale={0.97}
                className={styles.link}
                onClick={() => onOpenReport(ev.report_link.report_id, displayTitle(ev.report_link))}
              >
                原始研報 →
              </Pressable>
            </div>
          </div>
        </article>
      ))}
      {showAll && isLoadingMore && events.length <= PREVIEW ? (
        <div className={styles.loading} role="status">正在載入完整事件…</div>
      ) : null}
      {showAll && loadError ? (
        <div className={styles.loadError} role="alert">
          <span>載入完整事件失敗，已保留目前可用內容。</span>
          {onRetryLoad ? (
            <Pressable type="button" className={styles.loadMore} onClick={onRetryLoad}>
              重試載入事件
            </Pressable>
          ) : null}
        </div>
      ) : null}
      {showAll && !loadError && hasMore && onLoadMore ? (
        <div className={styles.loadMoreWrap}>
          <Pressable
            type="button"
            className={styles.loadMore}
            disabled={isLoadingMore}
            onClick={onLoadMore}
          >
            {isLoadingMore ? '載入中…' : `載入更多（尚有 ${remaining} 項）`}
          </Pressable>
        </div>
      ) : null}
    </div>
  )
}
