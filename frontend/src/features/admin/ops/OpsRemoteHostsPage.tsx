import { Card, PageHeader, Status, type LampTone } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type { OpsRemoteHost } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { OpsQueryError } from './OpsShared'
import { SUMMARY_LABELS, TIER_HINTS, TIER_LABELS, TIERS } from './opsLabels'
import {
  FEED_RESULT_LABELS, PUSH_LABELS, REMOTE_ALERT_HINTS, REMOTE_STATE_LABELS, ROUND_STATE_LABELS, SOURCE_LABELS,
  fmtAge, remoteTone, type RemoteTone,
} from './remoteHostLabels'
import { useOpsRemoteHosts } from './useOps'
import styles from './RemoteHosts.module.css'
import { t } from '@/i18n'

type RemoteService = NonNullable<OpsRemoteHost['services']>[number]
type RemoteSync = NonNullable<OpsRemoteHost['sync']>

const STATE_TONE: Record<RemoteTone, LampTone> = { ok: 'ok', alert: 'bad', unknown: 'idle' }

const SUMMARY_TONE: Record<RemoteService['summary'], LampTone> = {
  running: 'ok', idle: 'idle', failed: 'bad', transitioning: 'info', not_found: 'bad', unknown: 'idle',
}

function Verdict({ host }: { host: OpsRemoteHost }) {
  if (host.reasons.length > 0) {
    return (
      <div className={`${c.banner} ${styles.verdict}`} data-tone="bad" role="alert">
        <p><b>{t('admin.ops.opsRemoteHostsPage.sourceIssue')}</b>{t('admin.ops.opsRemoteHostsPage.text')}{host.reasons.map(r => REMOTE_STATE_LABELS[r]).join(t('admin.ops.opsRemoteHostsPage.text2'))}</p>
        {host.reasons.map(r => (
          <p key={r}><b>{REMOTE_STATE_LABELS[r]}</b>{t('admin.ops.opsRemoteHostsPage.text3', { REMOTE_ALERT_HINTS: REMOTE_ALERT_HINTS[r] })}</p>
        ))}
        {host.error && <p>{host.error}</p>}
      </div>
    )
  }
  if (host.state === 'ok') {
    return <p className={c.banner} data-tone="ok" role="status">{t('admin.ops.opsRemoteHostsPage.theSourceHostIsReporting')}</p>
  }
  return (
    <p className={c.banner} data-tone="warn" role="status">
      {t('admin.ops.opsRemoteHostsPage.theSourceHostSState')}{host.error ? t('admin.ops.opsRemoteHostsPage.text4', { error: host.error }) : ''}{t('admin.ops.opsRemoteHostsPage.thatDoesnTMeanIt')}
    </p>
  )
}

function SyncFacts({ sync }: { sync: RemoteSync }) {
  const timer = sync.timer_active == null ? t('admin.ops.opsRemoteHostsPage.unknownThatHostSOps') : sync.timer_active ? t('admin.ops.opsRemoteHostsPage.enabled') : t('admin.ops.opsRemoteHostsPage.disabled')
  return (
    <dl className={`${c.kv} ${styles.facts}`}>
      <dt>{t('admin.ops.opsRemoteHostsPage.newFileSource')}</dt><dd>{SOURCE_LABELS[sync.source]}</dd>
      <dt>{t('admin.ops.opsRemoteHostsPage.syncSchedule')}</dt><dd>{timer}</dd>
      <dt>{t('admin.ops.opsRemoteHostsPage.latestRound')}</dt>
      <dd>
        {ROUND_STATE_LABELS[sync.round_state]}
        {sync.round_started_at && t('admin.ops.opsRemoteHostsPage.started', { round_started_at: fmtDateTime(sync.round_started_at) })}
        {sync.round_rc != null && sync.round_rc !== 0 && t('admin.ops.opsRemoteHostsPage.exitCode', { round_rc: sync.round_rc })}
      </dd>
      <dt>{t('admin.ops.opsRemoteHostsPage.nasFetch')}</dt>
      <dd>
        {sync.feed_result ? FEED_RESULT_LABELS[sync.feed_result] : t('admin.ops.opsRemoteHostsPage.noResultYet')}
        {sync.feed_at && t('admin.ops.opsRemoteHostsPage.text5', { feed_at: fmtDateTime(sync.feed_at) })}
      </dd>
      <dt>{t('admin.ops.opsRemoteHostsPage.newFilesThisRound')}</dt><dd>{sync.new_files ?? '—'}</dd>
      <dt>{t('admin.ops.opsRemoteHostsPage.pushToR2')}</dt>
      <dd>{!sync.inbox_push ? t('admin.ops.opsRemoteHostsPage.offSyncInboxPush') : sync.push ? PUSH_LABELS[sync.push] : t('admin.ops.opsRemoteHostsPage.noResultYet')}</dd>
      <dt>{t('admin.ops.opsRemoteHostsPage.batchesPendingRetry')}</dt><dd>{sync.pending_replays}</dd>
      <dt>{t('admin.ops.opsRemoteHostsPage.lastFullySuccessfulRound')}</dt><dd>{fmtDateTime(sync.last_success_at)}</dd>
    </dl>
  )
}

function ServiceTable({ items }: { items: RemoteService[] }) {
  return (
    <div className={c.tableWrap}>
      <table className={c.table}>
        <thead>
          <tr><th>{t('admin.ops.opsRemoteHostsPage.service')}</th><th>{t('admin.ops.opsRemoteHostsPage.status')}</th><th>{t('admin.ops.opsRemoteHostsPage.lastRun')}</th><th>{t('admin.ops.opsRemoteHostsPage.schedule')}</th></tr>
        </thead>
        <tbody>
          {items.map(s => (
            <tr key={s.name} data-testid={`remote-service-${s.name}`} data-health={s.health}
              className={s.health === 'down' ? c.rowBad : undefined}>
              <td>
                <span className={`${c.mono} ${c.t1}`}>{s.name}</span>
                <div className={c.sub}>{s.description}</div>
              </td>
              <td>
                <Status tone={SUMMARY_TONE[s.summary]}>{SUMMARY_LABELS[s.summary]}</Status>
                {s.health !== 'ok' && s.health_reason && <div className={styles.reason}>{s.health_reason}</div>}
              </td>
              <td className={c.num}>{fmtDateTime(s.last_run_at)}</td>
              <td className={c.num}>
                {s.timer == null ? <span className={c.muted}>—</span> : (
                  <>
                    <div>{s.timer_active == null ? '—' : s.timer_active ? t('admin.ops.opsRemoteHostsPage.enabled') : t('admin.ops.opsRemoteHostsPage.disabled')}</div>
                    <div className={c.sub} title={t('admin.ops.opsRemoteHostsPage.nextTrigger')}>{t('admin.ops.opsRemoteHostsPage.next', { next_run_at: fmtDateTime(s.next_run_at) })}</div>
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
    <section className={c.card} aria-labelledby={titleId}>
      <div className={c.cardHead}>
        <h2 id={titleId} className={`${c.cardTitle} ${styles.hostTitle}`}>
          {t('admin.ops.opsRemoteHostsPage.sourceHost')}<span className={c.mono}>{host.name}</span>{host.host ? t('admin.ops.opsRemoteHostsPage.text6', { host: host.host }) : ''}
          <Status tone={STATE_TONE[remoteTone(host.state)]}>{REMOTE_STATE_LABELS[host.state]}</Status>
        </h2>
      </div>
      <div className={`${c.cardBody} ${styles.hostBody}`}>
        <p className={c.meta}>
          <span>{t('admin.ops.opsRemoteHostsPage.lastReport')}{' '}<b>{fmtDateTime(host.published_at)}</b>{host.age_seconds != null && t('admin.ops.opsRemoteHostsPage.text7', { age_seconds: fmtAge(host.age_seconds) })}</span>
          <span>{t('admin.ops.opsRemoteHostsPage.reportsEvery10MinutesNo', { stale_after_minutes: host.stale_after_minutes })}</span>
          <span>{t('admin.ops.opsRemoteHostsPage.checkedAtRefreshesEveryMinute', { checked_at: fmtDateTime(host.checked_at) })}</span>
        </p>
        <Verdict host={host} />

        {host.sync && (
          <div className={styles.group} role="region" aria-label={t('admin.ops.opsRemoteHostsPage.nasFetchAndUpload')}>
            <h3 className={styles.groupTitle}>
              {t('admin.ops.opsRemoteHostsPage.nasFetchAndUpload')}
              <span className={styles.groupHint}>
                {t('admin.ops.opsRemoteHostsPage.oneRoundEvery3Hours', { feed_stale_after_hours: host.feed_stale_after_hours })}
              </span>
            </h3>
            <SyncFacts sync={host.sync} />
          </div>
        )}

        {host.services == null ? (
          host.published_at != null && (
            <p className={c.banner} data-tone="warn">
              {t('admin.ops.opsRemoteHostsPage.thisReportDidnTInclude')}{host.services_error ? t('admin.ops.opsRemoteHostsPage.text8', { services_error: host.services_error }) : ''}{t('admin.ops.opsRemoteHostsPage.theNasFetchAndUpload')}
            </p>
          )
        ) : (
          <div className={styles.group} role="region" aria-label={t('admin.ops.opsRemoteHostsPage.servicesOnThatHost')}>
            <h3 className={styles.groupTitle}>
              {t('admin.ops.opsRemoteHostsPage.servicesOnThatHost')}
              <span className={styles.groupHint}>
                {t('admin.ops.opsRemoteHostsPage.stateAsOfTheLast', { length: host.services.length })}{down > 0 ? t('admin.ops.opsRemoteHostsPage.down', { down }) : ''}{t('admin.ops.opsRemoteHostsPage.onlyTheSectionAboveAlerts')}
              </span>
            </h3>
            {TIERS.map(tier => {
              const inTier = (host.services ?? []).filter(s => s.tier === tier)
              if (inTier.length === 0) return null
              return (
                <div key={tier} className={styles.tier} role="region" aria-label={t('admin.ops.opsRemoteHostsPage.services', { TIER_LABELS: TIER_LABELS[tier] })}>
                  <h4 className={styles.tierTitle}>
                    {TIER_LABELS[tier]}<span className={styles.groupHint}>{TIER_HINTS[tier]}</span>
                  </h4>
                  <ServiceTable items={inTier} />
                </div>
              )
            })}
          </div>
        )}
      </div>
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
  const header = (
    <PageHeader
      summary={t('admin.ops.opsRemoteHostsPage.whetherTheHostThatPushes')}
      help={(
        <>
          {t('admin.ops.opsRemoteHostsPage.ifThisHostSNew')}
        </>
      )}
    />
  )
  if (q.isPending) return <>{header}<p className={c.empty}>{t('admin.ops.opsRemoteHostsPage.loading')}</p></>
  if (q.isError) return <>{header}<OpsQueryError error={q.error} what={t('admin.ops.opsRemoteHostsPage.sourceHosts')} /></>
  if (q.data.items.length === 0) {
    return (
      <>
        {header}
        <Card>
          <p className={c.empty}>{t('admin.ops.opsRemoteHostsPage.thisHostHasNoSource')}</p>
          <p className={`${c.help} ${styles.setupHint}`}>
            {t('admin.ops.opsRemoteHostsPage.onlyNeededForDeploymentsWhere')}{' '}<code>HOST_BEACON_WATCH</code>{t('admin.ops.opsRemoteHostsPage.inThisHostSEnvironment')}{' '}<code>docs/production_resilience.md</code>{t('admin.ops.opsRemoteHostsPage.sourceHostBeacon')}
          </p>
        </Card>
      </>
    )
  }
  return <>{header}{q.data.items.map(host => <HostCard key={host.name} host={host} />)}</>
}
