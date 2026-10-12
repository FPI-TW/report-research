import { Link } from 'react-router'
import { fmtDateTime } from '../auditLabels'
import { OpsQueryError, SummaryBadge } from './OpsShared'
import { TIER_HINTS, TIER_LABELS, TIERS, needsAttention } from './opsLabels'
import { useOpsServices } from './useOps'
import adminStyles from '../Admin.module.css'
import styles from './Ops.module.css'

/** 總覽：環境與主機、各層運作中／總數、需要注意的服務。 */
export default function OpsOverviewPage() {
  const q = useOpsServices()
  if (q.isPending) return <p className={adminStyles.idle}>載入中…</p>
  if (q.isError) return <OpsQueryError error={q.error} what="服務狀態" />
  const { items } = q.data
  const problems = items.filter(needsAttention)
  return (
    <>
      <section className={adminStyles.card} aria-labelledby="ops-overview-title">
        <h2 id="ops-overview-title" className={adminStyles.ctitle}>總覽</h2>
        <p className={styles.meta}>
          <span>環境 <b>{q.data.environment}</b></span>
          <span>主機 <b>{q.data.host}</b></span>
          <span>檢查時間 <b>{fmtDateTime(q.data.checked_at)}</b></span>
        </p>
        <div className={adminStyles.spacer} />
        {items.length === 0 ? (
          <p className={adminStyles.idle}>Service Catalog 裡沒有任何服務</p>
        ) : (
          <div className={styles.tierGrid}>
            {TIERS.map(tier => {
              const inTier = items.filter(s => s.tier === tier)
              const running = inTier.filter(s => s.summary === 'running').length
              return (
                <div key={tier} className={styles.tierCard} aria-label={`${TIER_LABELS[tier]}服務`}>
                  <div className={styles.tierName}>{TIER_LABELS[tier]}</div>
                  <div className={styles.tierCount}>{running}／{inTier.length}</div>
                  <div className={styles.tierHint}>運作中／總數・{TIER_HINTS[tier]}</div>
                </div>
              )
            })}
          </div>
        )}
      </section>
      <section className={adminStyles.card} aria-labelledby="ops-problems-title">
        <h2 id="ops-problems-title" className={adminStyles.ctitle}>需要注意</h2>
        {problems.length === 0 ? (
          <p className={adminStyles.idle}>{items.length === 0 ? '—' : '目前沒有失敗或停擺的服務'}</p>
        ) : (
          <ul className={styles.problems}>
            {problems.map(s => (
              <li key={s.name}>
                <SummaryBadge summary={s.summary} />
                <Link to={`../services/${encodeURIComponent(s.name)}`} relative="path">{s.name}</Link>
                <span className={adminStyles.muted}>{TIER_LABELS[s.tier]}・{s.description || s.target}</span>
              </li>
            ))}
          </ul>
        )}
        <div className={styles.links}>
          <Link className={adminStyles.action} to="../services" relative="path">所有服務</Link>
          <Link className={adminStyles.action} to="../logs" relative="path">查看日誌</Link>
        </div>
      </section>
    </>
  )
}
