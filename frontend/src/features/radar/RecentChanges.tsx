import { useId } from 'react'
import { displayTitle } from '../../lib/displayTitle'
import type { EventCard } from '../../lib/radarSchemas'
import { changeChip } from './changeText'
import { fmtShortDate } from './radarFormat'
import shared from './shared.module.css'
import styles from './RecentChanges.module.css'

/** 收合時顯示的事件數。 */
export const EVENT_PREVIEW = 3

interface Props {
  events: EventCard[]
  total: number
  onOpenReport: (reportId: string, title?: string | null) => void
  showAll: boolean
  onToggleAll: () => void
  hasMore?: boolean
  remaining?: number
  isLoadingMore?: boolean
  loadError?: boolean
  onLoadMore?: () => void
  onRetryLoad?: () => void
}

/**
 * 近期變化事件卡：日期與券商、一句話標題、變動膠囊（調升紅、調降綠）。點卡片開該份研報。
 * 預設只列前三則；「查看全部」再向事件 API 分頁載入。
 */
export function RecentChanges({
  events, total, onOpenReport, showAll, onToggleAll,
  hasMore, remaining = 0, isLoadingMore, loadError, onLoadMore, onRetryLoad,
}: Props) {
  const id = useId()
  const listId = `${id}-list`
  const visible = showAll ? events : events.slice(0, EVENT_PREVIEW)

  return (
    <section className={styles.section} aria-labelledby={id}>
      {/* 「查看全部」放在 h2 外面：放進去會被串進標題的可及名稱，而且展開後標題會跟著改名。 */}
      <div className={styles.headRow}>
        <h2 id={id} className={styles.heading}>近期變化</h2>
        {total > EVENT_PREVIEW ? (
          <button
            type="button"
            className={styles.toggle}
            aria-expanded={showAll}
            aria-controls={listId}
            onClick={onToggleAll}
          >
            {showAll ? '收合' : `查看全部 ${total} 則`}
          </button>
        ) : null}
      </div>

      {events.length ? (
        <div className={styles.list} id={listId}>
          {visible.map((ev, i) => {
            const broker = ev.broker_display || ev.broker || '未標示券商'
            return (
              <button
                key={`${ev.report_link.report_id}-${ev.report_date}-${i}`}
                type="button"
                className={`${shared.tile} ${styles.card}`}
                onClick={() => onOpenReport(ev.report_link.report_id, displayTitle(ev.report_link))}
              >
                <span className={styles.meta}>
                  <span className={shared.num}>{fmtShortDate(ev.report_date)}</span>
                  <span>{broker}</span>
                </span>
                <span className={styles.headline}>{ev.headline}</span>
                {ev.changes.length ? (
                  <span className={styles.chips}>
                    {ev.changes.slice(0, 3).map((c, j) => {
                      const chip = changeChip(c)
                      return (
                        <span key={`${c.field}-${j}`} className={styles.chip} data-tone={chip.tone}>
                          {chip.label}
                          <span className={`${styles.chipValue} ${shared.num}`}>{chip.value}</span>
                        </span>
                      )
                    })}
                  </span>
                ) : null}
              </button>
            )
          })}
        </div>
      ) : (
        <p className={styles.empty}>這段期間沒有評等、目標價或觀點的變化</p>
      )}

      {showAll && isLoadingMore && events.length <= EVENT_PREVIEW ? (
        <p className={styles.status} role="status">載入中…</p>
      ) : null}
      {showAll && loadError ? (
        <div className={styles.more} role="alert">
          <span className={styles.status}>載入完整事件失敗</span>
          {onRetryLoad ? <button type="button" className={shared.btn} onClick={onRetryLoad}>重試</button> : null}
        </div>
      ) : null}
      {showAll && !loadError && hasMore && onLoadMore ? (
        <div className={styles.more}>
          <button type="button" className={shared.btn} disabled={isLoadingMore} onClick={onLoadMore}>
            {isLoadingMore ? '載入中…' : `載入更多（還有 ${remaining} 則）`}
          </button>
        </div>
      ) : null}
    </section>
  )
}
