import { Link } from 'react-router'
import type { LogEntry, Progress, UnitFailures } from '../../../../lib/progressSchema'
import { Card, Status, type LampTone } from '../../../../components/console'
import c from '../../../../components/console/Console.module.css'
import { useMonitoringMissing } from '../../adminCapabilities'
import { SECTION_ID, fmtMinute } from './attention'
import { Bar, Count, Row, TonePill } from './parts'
import { PIPELINE_ROWS } from './pipelineMeta'
import { fmtInt, ingestRateText, rateText, type Rates } from './rate'
import styles from './Pipeline.module.css'
import { t } from '@/i18n'

/**
 * 排程與執行：每 3 小時的 NAS 增量同步、哪些批次正在跑、進行中的進度、unit 失敗紀錄。
 *
 * 補的是兩個可觀測性斷層（理由見 docs/production_resilience.md「`unit_failures.log` 的讀取端」）：
 * runtime 區塊原本只認全量腳本的 log，生產實際的入庫路徑 sync 零可見度；`data/unit_failures.log`
 * 在 2026-07-28 停擺時寫了 10 筆卻零消費端。
 */
const SYNC_TONE: Record<string, LampTone> = { done: 'ok', running: 'info' }

function SyncRow({ sync }: { sync: LogEntry | null | undefined }) {
  if (sync === undefined) return <Row label={t('admin.ops.pipeline.scheduleCard.scheduledSync')}><p className={styles.idle}>{t('admin.ops.pipeline.scheduleCard.thisBackendVersionDoesnT')}</p></Row>
  if (sync === null) return <Row label={t('admin.ops.pipeline.scheduleCard.scheduledSync')}><p className={styles.idle}>{t('admin.ops.pipeline.scheduleCard.noSyncRecordsYet')}</p></Row>
  return (
    <Row label={t('admin.ops.pipeline.scheduleCard.scheduledSync')}>
      <div className={styles.line}>
        <Status tone={SYNC_TONE[sync.status] ?? 'idle'}>{sync.label}</Status>
        <span className={c.num}>{fmtMinute(sync.timestamp)}</span>
        <span className={c.muted}>{t('admin.ops.pipeline.scheduleCard.oneRoundEvery3Hours')}</span>
      </div>
      <pre className={`${c.log} ${styles.raw}`}>{sync.raw}</pre>
    </Row>
  )
}

/** web 必定在跑（不然看不到這頁），不列。 */
function BatchRow({ pipelines }: { pipelines: Progress['pipelines'] }) {
  const rows = PIPELINE_ROWS.filter(r => r.key !== 'web')
  const on = rows.filter(r => pipelines[r.key])
  const off = rows.filter(r => !pipelines[r.key])
  return (
    <Row label={t('admin.ops.pipeline.scheduleCard.batches')}>
      <div className={styles.chips} aria-label={t('admin.ops.pipeline.scheduleCard.runningBatches')}>
        {on.length > 0
          ? on.map(r => <TonePill key={r.key} tone="run">{t('admin.ops.pipeline.scheduleCard.running', { name: r.name })}</TonePill>)
          : <TonePill tone="idle">{t('admin.ops.pipeline.scheduleCard.noBatchesRunning')}</TonePill>}
      </div>
      {off.length > 0 && (
        <div className={`${c.muted} ${styles.small}`}>{t('admin.ops.pipeline.scheduleCard.notRun')}{off.map(r => r.name).join(t('admin.ops.pipeline.scheduleCard.text'))}</div>
      )}
    </Row>
  )
}

function ProgressLine({ name, pct, stat, rate }: { name: string; pct: number; stat: string; rate?: string }) {
  return (
    <div className={styles.prog}>
      <span className={styles.progName}>{name}</span>
      <Bar pct={pct} label={t('admin.ops.pipeline.scheduleCard.progress', { name })} gold />
      <span className={c.num}>{`${pct.toFixed(1)}%・${stat}`}</span>
      {rate && <span className={styles.progRate}>{rate}</span>}
    </div>
  )
}

/**
 * 進行中的進度，只在該批次執行中才出現（不常駐空的進度卡）。
 *
 * 匯入要同時看兩條路徑：全量 `ingest_all.py` 有篇數與失敗數（來自 `ingest_run_*.log`），增量
 * `sync_new_reports.py` 沒有進度表徵。全量優先：拿「增量匯入執行中」蓋掉具體數字是資訊量的倒退；
 * 反過來只有增量在跑時，寧可講一句沒有數字的實話，也不要講「無執行中的導入」這句假話。
 * 增量的文案刻意不取 `sync.label`：那塊解析殼層 log，手動直接跑 `.py` 時不會更新。
 */
function ProgressRow({ p, rates }: { p: Progress; rates: Rates }) {
  const lines = []
  if (p.pipelines.ingest) {
    const text = p.orchestrator?.label
      ?? (p.ingest ? t('admin.ops.pipeline.scheduleCard.ingestedThisRoundFailed', { ingested: fmtInt(p.ingest.ingested), fail: fmtInt(p.ingest.fail) }) : t('admin.ops.pipeline.scheduleCard.fullIngestRunning'))
    lines.push(
      <div key="ingest" className={styles.line}>
        <span className={styles.progName}>{t('admin.ops.pipeline.scheduleCard.fullIngest')}</span><span>{text}</span>
        <span className={`${c.muted} ${styles.small}`}>{ingestRateText(rates.rpm, rates.cps)}</span>
      </div>,
    )
  } else if (p.pipelines.sync_import) {
    lines.push(
      <div key="sync" className={styles.line}>
        <span className={styles.progName}>{t('admin.ops.pipeline.scheduleCard.incrementalImport')}</span><span>{t('admin.ops.pipeline.scheduleCard.running2')}</span>
        <span className={`${c.muted} ${styles.small}`}>{t('admin.ops.pipeline.scheduleCard.incrementalImportHasNoProgress')}</span>
      </div>,
    )
  }
  if (p.pipelines.tag && p.tagging) {
    const item = p.tagging
    lines.push(
      <ProgressLine key="tag" name={t('admin.ops.pipeline.scheduleCard.semanticTagging')} pct={item.pct} stat={t('admin.ops.pipeline.scheduleCard.failed', { done: fmtInt(item.done), total: fmtInt(item.total), fail: fmtInt(item.fail) })}
        rate={rateText(Math.max(0, item.total - item.done), rates.tpm, t('admin.ops.pipeline.scheduleCard.reports'))} />,
    )
  }
  if (p.pipelines.summaries) {
    const s = p.summary
    lines.push(
      <ProgressLine key="summary" name={t('admin.ops.pipeline.scheduleCard.summaryGeneration')} pct={s.pct} stat={t('admin.ops.pipeline.scheduleCard.remaining', { remaining: fmtInt(s.remaining) })}
        rate={rateText(s.remaining, rates.spm, t('admin.ops.pipeline.scheduleCard.reports'))} />,
    )
  }
  if (lines.length === 0) return null
  return <Row label={t('admin.ops.pipeline.scheduleCard.progress2')}>{lines}</Row>
}

function FailuresRow({ failures }: { failures: UnitFailures | undefined }) {
  if (!failures) return <Row label={t('admin.ops.pipeline.scheduleCard.scheduleFailures')}><p className={styles.idle}>{t('admin.ops.pipeline.scheduleCard.thisBackendVersionDoesnT2')}</p></Row>
  return (
    <Row label={t('admin.ops.pipeline.scheduleCard.scheduleFailures')}>
      <div className={styles.line}>
        <span>{t('admin.ops.pipeline.scheduleCard.last24Hours')}{' '}<Count value={failures.count_24h} tone="bad" />{' '}{t('admin.ops.pipeline.scheduleCard.times')}</span>
        <span>{t('admin.ops.pipeline.scheduleCard.inTheLast7Days', { count_7d: fmtInt(failures.count_7d) })}</span>
        <span className={c.muted}>{t('admin.ops.pipeline.scheduleCard.last', { latest: fmtMinute(failures.latest) })}</span>
      </div>
      {failures.recent.length > 0 && (
        <div className={`${c.tableWrap} ${styles.subTable}`}>
          <table className={c.table} aria-label={t('admin.ops.pipeline.scheduleCard.recentScheduleFailures')}>
            <thead><tr><th>{t('admin.ops.pipeline.scheduleCard.time')}</th><th>{t('admin.ops.pipeline.scheduleCard.service')}</th><th>{t('admin.ops.pipeline.scheduleCard.stage')}</th><th className={c.tdRight}>{t('admin.ops.pipeline.scheduleCard.exitCode')}</th></tr></thead>
            <tbody>
              {failures.recent.map((e, i) => (
                <tr key={`${e.ts ?? 'na'}-${i}`}>
                  <td className={c.num}>{fmtMinute(e.ts)}</td>
                  <td className={c.mono}>{e.unit}</td>
                  <td>{e.stage ?? '—'}</td>
                  <td className={`${c.num} ${c.tdRight}`}>
                    {e.rc === null ? <span className={c.muted}>{t('admin.ops.pipeline.scheduleCard.unknown')}</span> : e.rc}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Row>
  )
}

export function ScheduleCard({ progress: p, rates }: { progress: Progress; rates: Rates }) {
  // 排程工作頁讀監控匯入器的 DB 投影；這台沒裝就不放連結（導覽也不列那一頁）。
  const noJobsPage = useMonitoringMissing()
  return (
    <div id={SECTION_ID.schedule}>
      <Card
        title={t('admin.ops.pipeline.scheduleCard.scheduleAndRuns')}
        labelledBy="pipeline-schedule-title"
        right={noJobsPage ? undefined
          : <Link className={`${c.btn} ${c.btnSm}`} to="../jobs" relative="path">{t('admin.ops.pipeline.scheduleCard.viewScheduledJobs')}</Link>}
      >
        <div className={styles.body}>
          <div className={styles.rows}>
            <SyncRow sync={p.sync} />
            <BatchRow pipelines={p.pipelines} />
            <ProgressRow p={p} rates={rates} />
            <FailuresRow failures={p.unit_failures} />
          </div>
          <p className={styles.note}>
            {t('admin.ops.pipeline.scheduleCard.summariesTitlesAndTakeawaysAre')}
          </p>
        </div>
      </Card>
    </div>
  )
}
