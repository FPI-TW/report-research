import { Pressable } from '../../components/primitives/Pressable'
import { Skeleton } from '../../components/primitives/Skeleton'
import type { ChangeItem, Market, Window } from '../../lib/radarSchemas'
import { DirectionTag } from './DirectionTag'
import { fmtDate, fmtEps, fmtPrice, RATING_DISPLAY, WINDOW_LABEL } from './radarFormat'
import { useBrokerHistory } from './useRadar'
import styles from './BrokerTimeline.module.css'

interface Props {
  code: string
  market: Market
  broker: string
  brokerDisplay?: string | null
  window: Window
  expanded: boolean
  onCollapse: () => void
  onOpenReport: (reportId: string, fileName?: string | null) => void
}

function ChangeDetail({ change }: { change: ChangeItem }) {
  const incomparable = !change.comparable || change.direction === 'incomparable'
  return (
    <div className={styles.changeDetail}>
      <DirectionTag
        direction={change.direction}
        label={incomparable ? undefined : change.label}
        pct={incomparable ? null : change.pct_change}
      />
      {incomparable ? (
        <div className={styles.incomparableDetail}>
          <span>{change.prev_value || '—'} → {change.curr_value || '—'}</span>
          {change.incomparable_reason ? <span>{change.incomparable_reason}</span> : null}
        </div>
      ) : null}
    </div>
  )
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
        <Pressable tapScale={0.97} className={styles.link} onClick={() => q.refetch()}>重試</Pressable>
      </div>
    )
  }

  const data = q.data
  const latest = data.snapshots[0]
  const latestDiff = data.diffs[0]
  const hasPriorReport = latestDiff?.has_prior_report
    ?? Boolean(latestDiff?.from_report_id || latestDiff?.from_report_date || data.snapshots[1])
  const prior = !hasPriorReport
    ? undefined
    : latestDiff?.from_report_id
      ? data.snapshots.find(snapshot => snapshot.report_id === latestDiff.from_report_id)
      : latestDiff?.from_report_date
        ? data.snapshots.find(snapshot => snapshot.report_date === latestDiff.from_report_date)
        : data.snapshots[1]
  const priorLabel = latestDiff?.has_prior_comparable ? '前次可比較研報' : '前次研報'
  const priorNote = latestDiff?.note
    || (hasPriorReport ? '有前次研報，但沒有可比較欄位' : '沒有前次研報')
  const name = data.broker_display || brokerDisplay || broker

  if (data.coverage_state === 'pending_extraction') {
    return (
      <div className={styles.panel}>
        <div className={styles.head}>
          <div>
            <h3 className={styles.title}>{name}觀點歷程</h3>
            <div className={styles.meta}>此券商已有研報</div>
          </div>
          <Pressable className={styles.collapse} onClick={onCollapse}>收合</Pressable>
        </div>
        <p className={styles.pending} role="status">此券商研報尚待觀點資料整理</p>
      </div>
    )
  }

  return (
    <div className={styles.panel}>
      <div className={styles.head}>
        <div>
          <h3 className={styles.title}>{name}觀點歷程</h3>
          <div className={styles.meta}>
            目前評等 {RATING_DISPLAY[data.current_rating]} · {data.report_count} 份研報 · {WINDOW_LABEL[window]}
          </div>
        </div>
        <Pressable className={styles.collapse} onClick={onCollapse}>收合</Pressable>
      </div>

      {data.coverage_state === 'partial' ? (
        <p className={styles.quality}>部分研報仍在整理，以下只顯示已擷取內容。</p>
      ) : null}

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
                {latest.primary_eps || latest.eps[0]
                  ? fmtEps(
                      (latest.primary_eps ?? latest.eps[0]).median,
                      (latest.primary_eps ?? latest.eps[0]).currency,
                      (latest.primary_eps ?? latest.eps[0]).fiscal_year,
                      (latest.primary_eps ?? latest.eps[0]).period,
                      (latest.primary_eps ?? latest.eps[0]).unit,
                    )
                  : '—'}
              </span>
            </div>
          </div>
          <div className={styles.mid}>
            {latestDiff?.changes.length ? (
              latestDiff.changes.slice(0, 4).map((change, index) => (
                <ChangeDetail key={`${change.field}-${change.dimension ?? ''}-${index}`} change={change} />
              ))
            ) : (
              <span className={styles.note} style={{ margin: 0, textAlign: 'center' }}>
                {priorNote}
              </span>
            )}
          </div>
          <div className={styles.col}>
            <div className={styles.colLabel}>
              {prior ? `${priorLabel}（${fmtDate(prior.report_date)}）` : priorLabel}
            </div>
            {prior ? (
              <div className={styles.kv}>
                <span className={styles.k}>評等</span>
                <span className={styles.v}>{prior.rating_raw || RATING_DISPLAY[prior.rating]}</span>
                <span className={styles.k}>目標價</span>
                <span className={styles.v}>{fmtPrice(prior.target_price, prior.target_currency)}</span>
                <span className={styles.k}>EPS</span>
                <span className={styles.v}>
                  {prior.primary_eps || prior.eps[0]
                    ? fmtEps(
                        (prior.primary_eps ?? prior.eps[0]).median,
                        (prior.primary_eps ?? prior.eps[0]).currency,
                        (prior.primary_eps ?? prior.eps[0]).fiscal_year,
                        (prior.primary_eps ?? prior.eps[0]).period,
                        (prior.primary_eps ?? prior.eps[0]).unit,
                      )
                    : '—'}
                </span>
              </div>
            ) : (
              <p className={styles.note}>{priorNote}</p>
            )}
          </div>
        </div>
      ) : null}

      <ol className={styles.timeline}>
        {data.snapshots.map((snap, idx) => {
          const diff = data.diffs[idx]
          const evidence = snap.thesis.filter(item => item.evidence || item.summary)
          const primaryEps = snap.primary_eps ?? snap.eps[0]
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
                  EPS {primaryEps
                    ? fmtEps(
                        primaryEps.median,
                        primaryEps.currency,
                        primaryEps.fiscal_year,
                        primaryEps.period,
                        primaryEps.unit,
                      )
                    : '—'}
                </span>
              </div>
              {diff?.changes.length ? (
                <div className={styles.changes}>
                  {diff.changes.map((change, index) => (
                    <ChangeDetail
                      key={`${change.field}-${change.dimension ?? ''}-${index}`}
                      change={change}
                    />
                  ))}
                </div>
              ) : diff && !diff.has_prior_comparable ? (
                <p className={styles.note}>{diff.note || '無前次可比較研報'}</p>
              ) : null}
              {evidence.length ? (
                <div className={styles.evidenceList}>
                  {evidence.map(item => (
                    <div key={item.dimension} className={styles.evidence}>
                      <strong>{item.dimension_display}</strong>
                      {item.summary ? `：${item.summary}` : ''}
                      {item.evidence ? ` — ${item.evidence}` : ''}
                    </div>
                  ))}
                </div>
              ) : null}
              <Pressable
                tapScale={0.97}
                className={styles.link}
                onClick={() => onOpenReport(snap.report_link.report_id, snap.report_link.file_name)}
              >
                查看原始研報
              </Pressable>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
