import type { OpsRemoteHost } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { OpsQueryError, SummaryBadge } from './OpsShared'
import { TIER_HINTS, TIER_LABELS, TIERS } from './opsLabels'
import {
  FEED_RESULT_LABELS, PUSH_LABELS, REMOTE_ALERT_HINTS, REMOTE_STATE_LABELS, ROUND_STATE_LABELS, SOURCE_LABELS,
  fmtAge, remoteTone, type RemoteTone,
} from './remoteHostLabels'
import { useOpsRemoteHosts } from './useOps'
import adminStyles from '../Admin.module.css'
import styles from './Ops.module.css'

type RemoteService = NonNullable<OpsRemoteHost['services']>[number]
type RemoteSync = NonNullable<OpsRemoteHost['sync']>

const TONE_PILL: Record<RemoteTone, string> = {
  ok: styles.sRunning,
  alert: styles.sFailed,
  unknown: styles.sIdle,
}

function Verdict({ host }: { host: OpsRemoteHost }) {
  if (host.reasons.length > 0) {
    return (
      <div className={styles.depAlert} role="alert">
        <p className={styles.depAlertTitle}>
          <b>來源異常</b>：{host.reasons.map(r => REMOTE_STATE_LABELS[r]).join('、')}
        </p>
        {host.reasons.map(r => (
          <p key={r} className={styles.depAlertText}><b>{REMOTE_STATE_LABELS[r]}</b>：{REMOTE_ALERT_HINTS[r]}</p>
        ))}
        {host.error && <p className={styles.depAlertText}>{host.error}</p>}
      </div>
    )
  }
  if (host.state === 'ok') {
    return <p className={styles.depAllOk} role="status">來源主機有在回報，NAS 抓檔與推送到 R2 這一段正常。</p>
  }
  return (
    <p className={styles.warnNote} role="status">
      目前判斷不出來源主機的狀態{host.error ? `：${host.error}` : ''}。這不代表它壞了；R2 連不上時由物件儲存那一組告警。
    </p>
  )
}

function SyncFacts({ sync }: { sync: RemoteSync }) {
  const timer = sync.timer_active == null ? '不明（那台的維運代理沒有回報）' : sync.timer_active ? '啟用' : '停用'
  return (
    <dl className={styles.dl}>
      <dt>新檔來源</dt><dd>{SOURCE_LABELS[sync.source]}</dd>
      <dt>同步排程</dt><dd>{timer}</dd>
      <dt>最近一輪</dt>
      <dd>
        {ROUND_STATE_LABELS[sync.round_state]}
        {sync.round_started_at && `（${fmtDateTime(sync.round_started_at)} 開始）`}
        {sync.round_rc != null && sync.round_rc !== 0 && `，退出碼 ${sync.round_rc}`}
      </dd>
      <dt>抓取 NAS</dt>
      <dd>
        {sync.feed_result ? FEED_RESULT_LABELS[sync.feed_result] : '還沒有結果'}
        {sync.feed_at && `（${fmtDateTime(sync.feed_at)}）`}
      </dd>
      <dt>本輪新檔</dt><dd>{sync.new_files ?? '—'}</dd>
      <dt>推送到 R2</dt>
      <dd>{!sync.inbox_push ? '沒開（SYNC_INBOX_PUSH）' : sync.push ? PUSH_LABELS[sync.push] : '還沒有結果'}</dd>
      <dt>待補傳批次</dt><dd>{sync.pending_replays}</dd>
      <dt>最近一次整輪成功</dt><dd>{fmtDateTime(sync.last_success_at)}</dd>
    </dl>
  )
}

function ServiceTable({ items }: { items: RemoteService[] }) {
  return (
    <div className={adminStyles.tableWrap}>
      <table className={adminStyles.table}>
        <thead>
          <tr><th>服務</th><th>狀態</th><th>最近執行</th><th>排程</th></tr>
        </thead>
        <tbody>
          {items.map(s => (
            <tr key={s.name} data-testid={`remote-service-${s.name}`} data-health={s.health}>
              <td className={adminStyles.wrapCell}>
                {s.name}
                <div className={adminStyles.muted}>{s.description}</div>
              </td>
              <td>
                <SummaryBadge summary={s.summary} />
                {s.health !== 'ok' && s.health_reason && <div className={styles.svcErr}>{s.health_reason}</div>}
              </td>
              <td className={adminStyles.num}>{fmtDateTime(s.last_run_at)}</td>
              <td className={adminStyles.num}>
                {s.timer == null ? <span className={adminStyles.muted}>—</span> : (
                  <>
                    <div>{s.timer_active == null ? '—' : s.timer_active ? '啟用' : '停用'}</div>
                    <div className={adminStyles.muted} title="下次觸發">下次 {fmtDateTime(s.next_run_at)}</div>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function HostCard({ host }: { host: OpsRemoteHost }) {
  const titleId = `ops-remote-${host.name}`
  const down = (host.services ?? []).filter(s => s.health === 'down').length
  return (
    <section className={adminStyles.card} aria-labelledby={titleId}>
      <h2 id={titleId} className={adminStyles.ctitle}>
        來源主機：{host.name}{host.host ? `（${host.host}）` : ''}{' '}
        <span className={`${styles.pill} ${TONE_PILL[remoteTone(host.state)]}`}>{REMOTE_STATE_LABELS[host.state]}</span>
      </h2>
      <p className={styles.meta}>
        <span>最後回報 <b>{fmtDateTime(host.published_at)}</b>{host.age_seconds != null && `（${fmtAge(host.age_seconds)}）`}</span>
        <span>每 10 分鐘回報一次；超過 {host.stale_after_minutes} 分鐘沒有回報視為失聯</span>
        <span>檢查時間 {fmtDateTime(host.checked_at)}（每分鐘更新）</span>
      </p>
      <div className={adminStyles.spacer} />
      <Verdict host={host} />

      {host.sync && (
        <div className={styles.group} role="region" aria-label="NAS 抓檔與上傳">
          <h3 className={styles.groupTitle}>
            NAS 抓檔與上傳
            <span className={styles.groupHint}>
              每 3 小時一輪；超過 {host.feed_stale_after_hours} 小時沒有結果視為停擺
            </span>
          </h3>
          <SyncFacts sync={host.sync} />
        </div>
      )}

      {host.services == null ? (
        host.published_at != null && (
          <p className={styles.warnNote}>
            這次回報沒有帶服務清單{host.services_error ? `：${host.services_error}` : ''}。上面 NAS 抓檔與上傳的判斷不受影響。
          </p>
        )
      ) : (
        <div className={styles.group} role="region" aria-label="那台主機的服務">
          <h3 className={styles.groupTitle}>
            那台主機的服務
            <span className={styles.groupHint}>
              最後回報當下的狀態，共 {host.services.length} 項{down > 0 ? `、${down} 項故障` : ''}；只有上面那一段會告警，其餘由那台自己的告警負責
            </span>
          </h3>
          {TIERS.map(tier => {
            const inTier = (host.services ?? []).filter(s => s.tier === tier)
            if (inTier.length === 0) return null
            return (
              <div key={tier} className={styles.group} role="region" aria-label={`${TIER_LABELS[tier]}服務`}>
                <h3 className={styles.groupTitle}>
                  {TIER_LABELS[tier]}<span className={styles.groupHint}>{TIER_HINTS[tier]}</span>
                </h3>
                <ServiceTable items={inTier} />
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}

/**
 * 來源主機：這台的新研報若來自另一台主機（辦公室主機從 NAS 抓檔後推到 R2），那台每 10 分鐘把服務狀態與
 * 「NAS 抓檔與上傳」這一段的結果發布成信標，這頁顯示後端對它的判讀（`GET /api/admin/ops/remote-hosts`）。
 * 唯讀：那台主機的服務只能看，不能從這裡操作。
 */
export default function OpsRemoteHostsPage() {
  const q = useOpsRemoteHosts()
  if (q.isPending) return <p className={adminStyles.idle}>載入中…</p>
  if (q.isError) return <OpsQueryError error={q.error} what="來源主機" />
  if (q.data.items.length === 0) {
    return (
      <section className={adminStyles.card} aria-labelledby="ops-remote-none">
        <h2 id="ops-remote-none" className={adminStyles.ctitle}>來源主機</h2>
        <p className={adminStyles.idle}>這台沒有設定要監看的來源主機。</p>
        <p className={adminStyles.hint}>
          新研報由另一台主機推上來的部署才需要：在這台的環境檔設 <code>HOST_BEACON_WATCH</code>，並在來源那台啟用信標發布
          （步驟見 <code>docs/production_resilience.md</code>「來源主機信標」）。
        </p>
      </section>
    )
  }
  return <>{q.data.items.map(host => <HostCard key={host.name} host={host} />)}</>
}
