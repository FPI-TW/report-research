import { Fragment } from 'react'
import { Button, Card, Chip, PageHeader, StatTile, Status, type LampTone } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type {
  DataHealthResponse, DbAuditSection, FreshnessSection, R2ReconcileSection, ReconcileStats,
} from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { HEALTH_LABELS, healthTileTone } from './data/healthLabels'
import { HealthStatus } from './data/HealthStatus'
import { useRunnableServices } from './data/useRunnable'
import { OpsQueryError } from './OpsShared'
import OpsRetrievalRegressionCard from './OpsRetrievalRegressionCard'
import { useServiceActions } from './services/useServiceActions'
import { useDataHealth } from './useOps'
import styles from './data/Data.module.css'
import { t } from '@/i18n'

type FreshnessState = FreshnessSection['findings'][number]['state']

const FRESHNESS_LABELS: Record<FreshnessState, string> = {
  get fresh() { return t('admin.ops.opsDataHealthPage.fresh') }, get stale() { return t('admin.ops.opsDataHealthPage.stale') }, get suppressed() { return t('admin.ops.opsDataHealthPage.skippedNoNewReports') }, get disabled() { return t('admin.ops.opsDataHealthPage.onlyListed') }, get upstream_stale() { return t('admin.ops.opsDataHealthPage.pipelineDidnTFinish') },
}
const FRESHNESS_TONE: Record<FreshnessState, LampTone> = {
  fresh: 'ok', stale: 'bad', suppressed: 'idle', disabled: 'idle', upstream_stale: 'bad',
}
// 與 app/services/data_health.py 的 RECONCILE_FAIL_KEYS／RECONCILE_WARN_KEYS 對應（順序即顯示順序）。
const STAT_LABELS: [keyof ReconcileStats, string][] = [
  ['checked', '已檢查'], ['errors', '錯誤／連不上'], ['unkeyed', 'DB 缺 key'], ['key_mismatch', 'key 與正典不符'],
  ['missing', 'R2 缺檔'], ['size_mismatch', '大小不符'], ['sha_mismatch', 'SHA 不符'],
  ['sha_metadata_missing', '舊檔無 SHA metadata'], ['orphans', 'orphan（R2 有、DB 沒有）'],
]
const ISSUE_LABELS: Record<string, string> = {
  get error() { return t('admin.ops.opsDataHealthPage.errors') }, get unkeyed() { return t('admin.ops.opsDataHealthPage.missingKeyInDb') }, get key_mismatch() { return t('admin.ops.opsDataHealthPage.keyMismatch') }, get missing() { return t('admin.ops.opsDataHealthPage.missing') }, get size_mismatch() { return t('admin.ops.opsDataHealthPage.sizeMismatch') },
  get sha_mismatch() { return t('admin.ops.opsDataHealthPage.shaMismatch') }, get sha_metadata_missing() { return t('admin.ops.opsDataHealthPage.noShaMetadata') }, orphan: 'orphan',
}
const UNAVAILABLE: Record<string, string> = { get missing() { return t('admin.ops.opsDataHealthPage.noResultFileYet') }, get too_large() { return t('admin.ops.opsDataHealthPage.resultFileTooLargeNot') } }

/**
 * 頁首的「立即執行」：這三段各自對應的排程服務（Service Catalog 的名稱）。只有這台主機的 catalog 開放 run、
 * 而且有 `ops.operate` 時才顯示；送出前要確認＋重新驗證（`useServiceActions`，和服務頁同一套流程）。
 */
const RUN_SERVICES = ['freshness', 'audit', 'r2-reconcile'] as const

function fmtAge(days: number | null | undefined): string {
  if (days == null) return '—'
  if (days < 1) return t('admin.ops.opsDataHealthPage.hAgo', { v: (days * 24).toFixed(1) })
  return t('admin.ops.opsDataHealthPage.daysAgo', { days: days.toFixed(1) })
}

/** 結果檔類的段落在數字磚上的補充：最後一次的時間，或為什麼沒有。 */
function resultHint(section: DbAuditSection | R2ReconcileSection): string {
  if (!section.available) return UNAVAILABLE[section.unavailable_reason ?? ''] ?? t('admin.ops.opsDataHealthPage.resultFileUnreadable')
  return t('admin.ops.opsDataHealthPage.lastRun', { finished_at: fmtDateTime(section.finished_at), v: section.stale ? t('admin.ops.opsDataHealthPage.expired') : '' })
}

function ResultMeta({ section, schedule }: { section: DbAuditSection | R2ReconcileSection; schedule: string }) {
  if (!section.available) {
    const why = section.unavailable_reason ?? ''
    return (
      <p className={styles.idle}>
        {UNAVAILABLE[why] ?? t('admin.ops.opsDataHealthPage.resultFileUnreadable2', { why })}{t('admin.ops.opsDataHealthPage.itAppearsHereAfterFinishes', { schedule })}
      </p>
    )
  }
  return (
    <>
      <p className={c.meta}>
        <span>{t('admin.ops.opsDataHealthPage.lastRun2')}{' '}<b>{fmtDateTime(section.finished_at)}</b></span>
        {section.exit_code != null && <span>{t('admin.ops.opsDataHealthPage.exitCode')}{' '}<b>{section.exit_code}</b></span>}
        <span>{schedule}</span>
      </p>
      {section.stale && (
        <p className={c.banner} data-tone="warn">{t('admin.ops.opsDataHealthPage.theResultHasnTBeen')}</p>
      )}
    </>
  )
}

function FreshnessCard({ s }: { s: FreshnessSection }) {
  return (
    <Card title={t('admin.ops.opsDataHealthPage.batchFreshness')} labelledBy="dh-fresh-title" right={<HealthStatus status={s.status} />}>
      <div className={styles.body}>
        <p className={c.meta}>
          <span>{t('admin.ops.opsDataHealthPage.liveCheckSameRulesAs')}</span>
        </p>
        {s.error && <p className={c.error} role="alert">{t('admin.ops.opsDataHealthPage.onlyThePipelineHeartbeatCan', { error: s.error })}</p>}
      </div>
      <div className={c.tableWrap}>
        <table className={c.table} aria-label={t('admin.ops.opsDataHealthPage.batchFreshness')}>
          <thead><tr><th>{t('admin.ops.opsDataHealthPage.item')}</th><th>{t('admin.ops.opsDataHealthPage.status')}</th><th>{t('admin.ops.opsDataHealthPage.latestOutput')}</th><th>{t('admin.ops.opsDataHealthPage.threshold')}</th><th>{t('admin.ops.opsDataHealthPage.description')}</th></tr></thead>
          <tbody>
            {s.findings.map(f => (
              <tr key={f.asset}>
                <td className={c.nowrap}>{f.label}</td>
                <td><Status tone={FRESHNESS_TONE[f.state]}>{FRESHNESS_LABELS[f.state]}</Status></td>
                <td className={c.num}>
                  {fmtDateTime(f.latest)}
                  {f.age_days != null && <div className={c.sub}>{fmtAge(f.age_days)}</div>}
                </td>
                <td className={c.num}>
                  {f.threshold_days > 0 ? `${f.threshold_days} ${f.asset === 'pipeline' ? t('admin.ops.opsDataHealthPage.h') : t('admin.ops.opsDataHealthPage.days')}` : t('admin.ops.opsDataHealthPage.noAlert')}
                </td>
                <td><div className={styles.cellText}>{f.detail}</div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

function AuditCard({ s }: { s: DbAuditSection }) {
  return (
    <Card title={t('admin.ops.opsDataHealthPage.dataIntegrityAudit')} labelledBy="dh-audit-title" right={<HealthStatus status={s.status} />}>
      <div className={styles.body}>
        <ResultMeta section={s} schedule={t('admin.ops.opsDataHealthPage.runDailyAt0845')} />
        {s.error && <p className={c.error} role="alert">{s.error}</p>}
        {s.skipped.length > 0 && <p className={styles.note}>{t('admin.ops.opsDataHealthPage.skippedThisTime')}{s.skipped.join(t('admin.ops.opsDataHealthPage.text'))}</p>}
      </div>
      {s.findings.length > 0 && (
        <div className={c.tableWrap}>
          <table className={c.table} aria-label={t('admin.ops.opsDataHealthPage.auditChecks')}>
            <thead><tr><th>{t('admin.ops.opsDataHealthPage.check')}</th><th>{t('admin.ops.opsDataHealthPage.level')}</th><th className={c.tdRight}>{t('admin.ops.opsDataHealthPage.violations')}</th><th>{t('admin.ops.opsDataHealthPage.remedy')}</th></tr></thead>
            <tbody>
              {s.findings.map(f => (
                <tr key={f.key}>
                  <td>
                    <div className={styles.cellText}>{f.label}</div>
                    <div className={`${c.sub} ${c.mono}`}>{f.key}</div>
                  </td>
                  <td className={c.nowrap}>{f.severity === 'error' ? t('admin.ops.opsDataHealthPage.errors') : t('admin.ops.opsDataHealthPage.warning')}</td>
                  <td className={c.tdRight}><Chip tone={f.count > 0 ? 'bad' : 'ok'} mono>{f.count}</Chip></td>
                  <td>
                    {f.count > 0 ? <div className={styles.cellText}>{f.detail}</div> : <span className={c.muted}>{t('admin.ops.opsDataHealthPage.clean')}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

function ReconcileCard({ s }: { s: R2ReconcileSection }) {
  return (
    <Card title={t('admin.ops.opsDataHealthPage.r2ObjectStorageReconciliation')} labelledBy="dh-r2-title" right={<HealthStatus status={s.status} />}>
      <div className={styles.body}>
        <ResultMeta section={s} schedule={t('admin.ops.opsDataHealthPage.runEveryMondayAt07')} />
        {s.available && s.mode === 'local' && (
          <p className={styles.note}>{t('admin.ops.opsDataHealthPage.objectStorageModeLocalNo')}</p>
        )}
        {s.stats && (
          <dl className={`${c.kv} ${styles.kvWide}`} aria-label={t('admin.ops.opsDataHealthPage.reconciliationCounts')}>
            {STAT_LABELS.map(([key, label]) => (
              <Fragment key={key}>
                <dt>{label}</dt>
                <dd>{s.stats![key]}</dd>
              </Fragment>
            ))}
            <dt>{t('admin.ops.opsDataHealthPage.orphanScan')}</dt>
            <dd>{s.orphan_scan === 'done' ? t('admin.ops.opsDataHealthPage.completed') : s.orphan_scan === 'error' ? t('admin.ops.opsDataHealthPage.failed') : t('admin.ops.opsDataHealthPage.skippedLimitSet')}</dd>
          </dl>
        )}
        {s.issues.length > 0 && (
          <details className={styles.details}>
            <summary>
              {t('admin.ops.opsDataHealthPage.sampleIssues', { length: s.issues.length })}{s.issues_total > s.issues.length && t('admin.ops.opsDataHealthPage.totalSeeThatRunS', { issues_total: s.issues_total })}
            </summary>
            <ul className={styles.list}>
              {s.issues.map((i, n) => (
                <li key={`${i.type}-${i.ref}-${n}`}>
                  <Chip tone="idle">{ISSUE_LABELS[i.type] ?? i.type}</Chip>
                  <code>{i.ref}</code>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </Card>
  )
}

function Tiles({ d }: { d: DataHealthResponse }) {
  const stale = d.freshness.findings.filter(f => f.state === 'stale' || f.state === 'upstream_stale').length
  const tiles = [
    { name: t('admin.ops.opsDataHealthPage.overall'), status: d.overall, hint: t('admin.ops.opsDataHealthPage.generatedAtCached60S', { generated_at: fmtDateTime(d.generated_at) }) },
    { name: t('admin.ops.opsDataHealthPage.freshness'), status: d.freshness.status,
      hint: stale > 0 ? t('admin.ops.opsDataHealthPage.staleOf', { stale, length: d.freshness.findings.length }) : t('admin.ops.opsDataHealthPage.liveCheckItems', { length: d.freshness.findings.length }) },
    { name: t('admin.ops.opsDataHealthPage.integrityAudit'), status: d.db_audit.status, hint: resultHint(d.db_audit) },
    { name: t('admin.ops.opsDataHealthPage.r2Reconciliation'), status: d.r2_reconcile.status, hint: resultHint(d.r2_reconcile) },
  ]
  return (
    <div className={`${c.tiles} ${styles.wideTiles}`}>
      {tiles.map(item => (
        <StatTile key={item.name} name={item.name} label={item.name} value={HEALTH_LABELS[item.status]} tone={healthTileTone(item.status)}
          sub={item.hint} subTitle={item.hint} />
      ))}
    </div>
  )
}

/**
 * 資料健康：批次新鮮度（即時）、資料完整性稽核與 R2 對帳（兩支 timer 最後一次的結果檔），下方另有檢索回歸。
 * 讀取全部唯讀；稽核與對帳太重，web 不會替你跑——頁首的「立即執行」是請維運代理觸發對應的排程服務一次
 * （等同 timer 觸發；需要 `ops.operate`、catalog 開放 run，執行前重新驗證），結果檔寫好後這頁才會更新。
 */
export default function OpsDataHealthPage() {
  const q = useDataHealth()
  const ops = useServiceActions()
  const runnable = useRunnableServices(ops.canOperate)
  const header = (
    <PageHeader
      summary={t('admin.ops.opsDataHealthPage.batchFreshnessDataIntegrityAudit')}
      help={(
        <>
          {t('admin.ops.opsDataHealthPage.batchFreshnessIsCheckedLive')}{' '}<code>ops.operate</code>{t('admin.ops.opsDataHealthPage.plusReVerificationBeforeRunning')}
        </>
      )}
      actions={(
        <>
          {RUN_SERVICES.filter(name => runnable.has(name)).map(name => (
            <Button key={name} disabled={ops.busy} onClick={() => ops.request(name, 'run')}>{t('admin.ops.opsDataHealthPage.runNow', { name })}</Button>
          ))}
          <Button onClick={() => { void q.refetch() }} disabled={q.isFetching}>
            {q.isFetching ? t('admin.ops.opsDataHealthPage.refreshing') : t('admin.ops.opsDataHealthPage.refresh')}
          </Button>
        </>
      )}
    />
  )
  const d = q.data
  return (
    <>
      {header}
      {q.isPending ? <p className={c.empty}>{t('admin.ops.opsDataHealthPage.loading')}</p> : q.isError || !d ? (
        <OpsQueryError error={q.error} what={t('admin.ops.opsDataHealthPage.dataHealth')} />
      ) : (
        <>
          <Tiles d={d} />
          <FreshnessCard s={d.freshness} />
          <AuditCard s={d.db_audit} />
          <ReconcileCard s={d.r2_reconcile} />
          <OpsRetrievalRegressionCard />
        </>
      )}
      {ops.dialogs}
    </>
  )
}
