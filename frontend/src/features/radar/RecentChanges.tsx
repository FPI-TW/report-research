import { Icon } from '../../components/primitives/Icon'
import { Pressable } from '../../components/primitives/Pressable'
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
}

const PREVIEW = 3

export function RecentChanges({ events, total, onOpenReport, showAll, onToggleAll }: Props) {
  const visible = showAll ? events : events.slice(0, PREVIEW)

  if (!events.length) {
    return <div className={styles.empty}>此窗期尚無實質觀點變化事件。</div>
  }

  return (
    <div>
      <div className={styles.list}>
        {visible.map((ev, i) => (
          <article key={`${ev.report_link.report_id}-${ev.report_date}-${i}`} className={styles.card}>
            <div className={styles.top}>
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
            {ev.evidence[0] ? (
              <div className={styles.evidence}>
                <Icon name="quote" size={14} className={styles.quoteIcon} />
                <span className={styles.evidenceText}>{ev.evidence[0]}</span>
              </div>
            ) : null}
            <Pressable
              tapScale={0.97}
              className={styles.link}
              onClick={() => onOpenReport(ev.report_link.report_id, ev.report_link.file_name)}
            >
              查看原始研報
            </Pressable>
          </article>
        ))}
      </div>
      {total > PREVIEW && onToggleAll ? (
        <div style={{ marginTop: 10, textAlign: 'right' }}>
          <Pressable tapScale={0.97} className={styles.link} onClick={onToggleAll}>
            {showAll ? '收合' : `查看全部 ${total} 項`}
          </Pressable>
        </div>
      ) : null}
    </div>
  )
}
