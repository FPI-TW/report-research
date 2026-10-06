import { Link } from 'react-router'
import type { OpsServiceStatus } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { OpsQueryError, SummaryBadge } from './OpsShared'
import { TIER_HINTS, TIER_LABELS, TIERS, lastRunAt, resultText, stateText } from './opsLabels'
import { useOpsServices } from './useOps'
import adminStyles from '../Admin.module.css'
import styles from './Ops.module.css'

function TimerCell({ s }: { s: OpsServiceStatus }) {
  if (!s.timer) return <span className={adminStyles.muted}>—</span>
  const t = s.timer_state
  return (
    <>
      <div>{t?.active_state ?? '—'}</div>
      <div className={adminStyles.muted} title="上次觸發">上次 {fmtDateTime(t?.last_trigger_at)}</div>
      <div className={adminStyles.muted} title="下次觸發">下次 {fmtDateTime(t?.next_elapse_at)}</div>
    </>
  )
}

function ServiceTable({ items }: { items: OpsServiceStatus[] }) {
  return (
    <div className={adminStyles.tableWrap}>
      <table className={adminStyles.table}>
        <thead>
          <tr><th>服務</th><th>狀態</th><th>ActiveState</th><th>Result</th><th>最近執行</th><th>Timer</th><th>操作</th></tr>
        </thead>
        <tbody>
          {items.map(s => (
            <tr key={s.name}>
              <td className={adminStyles.wrapCell}>
                <Link to={encodeURIComponent(s.name)}>{s.name}</Link>
                <div className={adminStyles.muted}>{s.description || s.target}</div>
              </td>
              <td>
                <SummaryBadge summary={s.summary} />
                {s.error && <div className={styles.svcErr}>{s.error}</div>}
              </td>
              <td>{stateText(s)}</td>
              <td>{resultText(s)}</td>
              <td className={adminStyles.num}>{fmtDateTime(lastRunAt(s))}</td>
              <td className={adminStyles.num}><TimerCell s={s} /></td>
              <td>
                {s.actions.includes('logs') && (
                  <Link className={adminStyles.action} to={`../logs?service=${encodeURIComponent(s.name)}`} relative="path">
                    日誌
                  </Link>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** 服務：依 tier 分組（核心／重要／輔助），每列 ActiveState、Result、最近執行與 timer。 */
export default function OpsServicesPage() {
  const q = useOpsServices()
  if (q.isPending) return <p className={adminStyles.idle}>載入中…</p>
  if (q.isError) return <OpsQueryError error={q.error} what="服務清單" />
  const { items } = q.data
  return (
    <section className={adminStyles.card} aria-labelledby="ops-services-title">
      <h2 id="ops-services-title" className={adminStyles.ctitle}>服務</h2>
      <p className={styles.meta}>
        <span>{q.data.environment}・{q.data.host}</span>
        <span>檢查時間 <b>{fmtDateTime(q.data.checked_at)}</b>（每 30 秒更新）</span>
      </p>
      <div className={adminStyles.spacer} />
      {items.length === 0 ? (
        <p className={adminStyles.idle}>Service Catalog 裡沒有任何服務</p>
      ) : (
        TIERS.map(tier => {
          const inTier = items.filter(s => s.tier === tier)
          if (inTier.length === 0) return null
          return (
            <div key={tier} className={styles.group} role="region" aria-label={`${TIER_LABELS[tier]}服務`}>
              <h3 className={styles.groupTitle}>
                {TIER_LABELS[tier]}<span className={styles.groupHint}>{TIER_HINTS[tier]}</span>
              </h3>
              <ServiceTable items={inTier} />
            </div>
          )
        })
      )}
    </section>
  )
}
