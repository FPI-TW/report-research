import { Skeleton } from '../../components/primitives/Skeleton'
import type { Window } from '../../lib/radarSchemas'
import { DirectionTag } from './DirectionTag'
import { fmtDate, fmtPrice, RATING_DISPLAY, WINDOW_LABEL } from './radarFormat'
import { useBrokerHistory } from './useRadar'
import styles from './BrokerTimeline.module.css'

interface Props {
  code: string
  market: string
  broker: string
  brokerDisplay?: string | null
  window: Window
  expanded: boolean
  onCollapse: () => void
  onOpenReport: (reportId: string, fileName?: string | null) => void
}

export function BrokerTimeline({
  code, market, broker, brokerDisplay, window, expanded, onCollapse, onOpenReport,
}: Props) {
  const q = useBrokerHistory(code, broker, market, window, expanded)

  if (!expanded) return null

  if (q.isLoading) {
    return (
      <div className={styles.panel} aria-busy="true" data-testid="broker-timeline-skeleton">
        <div className={styles.skel}>
          <Skeleton width={180} height={18} radius={4} />
          <Skeleton height={80} radius={8} />
          <Skeleton height={60} radius={8} />
          <Skeleton height={60} radius={8} />
        </div>
      </div>
    )
  }

  if (q.isError || !q.data) {
    return (
      <div className={styles.panel}>
        <p className={styles.error}>載入券商歷程失敗。</p>
        <button type="button" className={styles.link} onClick={() => q.refetch()}>重試</button>
      </div>
    )
  }

  const data = q.data
  const latest = data.snapshots[0]
  const prior = data.snapshots[1]
  const name = data.broker_display || brokerDisplay || broker

  return (
    <div className={styles.panel}>
      <div className={styles.head}>
        <div>
          <h3 className={styles.title}>{name}觀點歷程</h3>
          <div className={styles.meta}>
            目前評等 {RATING_DISPLAY[data.current_rating]} · {data.report_count} 份研報 · {WINDOW_LABEL[window]}
          </div>
        </div>
        <button type="button" className={styles.collapse} onClick={onCollapse}>收合</button>
      </div>

      {latest ? (
        <div className={styles.compare}>
          <div className={styles.col}>
            <div className={styles.colLabel}>最新研報（{fmtDate(latest.report_date)}）</div>
            <div className={styles.kv}>
              <span className={styles.k}>評等</span>
              <span className={styles.v}>{latest.rating_raw || RATING_DISPLAY[latest.rating]}</span>
              <span className={styles.k}>目標價</span>
              <span className={styles.v}>{fmtPrice(latest.target_price, latest.target_currency)}</span>
              <span className={styles.k}>EPS</span>
              <span className={styles.v}>
                {latest.eps[0]
                  ? `${fmtPrice(latest.eps[0].median, latest.eps[0].currency)}${latest.eps[0].fiscal_year ? ` FY${latest.eps[0].fiscal_year}` : ''}`
                  : '—'}
              </span>
            </div>
          </div>
          <div className={styles.mid}>
            {data.diffs[0]?.has_prior_comparable ? (
              data.diffs[0].changes.slice(0, 4).map((c, i) => (
                <DirectionTag key={i} direction={c.direction} label={c.label} pct={c.pct_change} />
              ))
            ) : (
              <span className={styles.note} style={{ margin: 0, textAlign: 'center' }}>
                {data.diffs[0]?.note || '此窗期內沒有前次可比較研報'}
              </span>
            )}
          </div>
          <div className={styles.col}>
            <div className={styles.colLabel}>
              {prior ? `前次可比較研報（${fmtDate(prior.report_date)}）` : '前次可比較研報'}
            </div>
            {prior ? (
              <div className={styles.kv}>
                <span className={styles.k}>評等</span>
                <span className={styles.v}>{prior.rating_raw || RATING_DISPLAY[prior.rating]}</span>
                <span className={styles.k}>目標價</span>
                <span className={styles.v}>{fmtPrice(prior.target_price, prior.target_currency)}</span>
                <span className={styles.k}>EPS</span>
                <span className={styles.v}>
                  {prior.eps[0]
                    ? `${fmtPrice(prior.eps[0].median, prior.eps[0].currency)}${prior.eps[0].fiscal_year ? ` FY${prior.eps[0].fiscal_year}` : ''}`
                    : '—'}
                </span>
              </div>
            ) : (
              <p className={styles.note}>此窗期內沒有前次可比較研報</p>
            )}
          </div>
        </div>
      ) : null}

      <ol className={styles.timeline}>
        {data.snapshots.map((snap, idx) => {
          const diff = data.diffs[idx]
          const evidence = snap.thesis.map(t => t.evidence).find(Boolean)
          return (
            <li key={snap.report_id} className={`${styles.node} ${snap.in_window ? '' : styles.outWindow}`}>
              <span className={styles.dot} aria-hidden />
              <div className={styles.nodeHead}>
                <span className={styles.date}>{fmtDate(snap.report_date)}</span>
                {!snap.in_window ? <span className={styles.badge}>窗外</span> : null}
              </div>
              <div className={styles.fields}>
                <span>評等 {snap.rating_raw || RATING_DISPLAY[snap.rating]}</span>
                <span>目標價 {fmtPrice(snap.target_price, snap.target_currency)}</span>
                <span>
                  EPS {snap.eps[0] ? fmtPrice(snap.eps[0].median, snap.eps[0].currency) : '—'}
                </span>
              </div>
              {diff?.changes.length ? (
                <div className={styles.changes}>
                  {diff.changes.map((c, i) => (
                    <DirectionTag key={i} direction={c.direction} label={c.label} pct={c.pct_change} />
                  ))}
                </div>
              ) : diff && !diff.has_prior_comparable ? (
                <p className={styles.note}>{diff.note || '無前次可比較研報'}</p>
              ) : null}
              {evidence ? <div className={styles.evidence}>{evidence}</div> : null}
              <button
                type="button"
                className={styles.link}
                onClick={() => onOpenReport(snap.report_link.report_id, snap.report_link.file_name)}
              >
                查看原始研報
              </button>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
