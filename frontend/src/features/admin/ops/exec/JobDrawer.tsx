import { Link } from 'react-router'
import type { JobItem } from '../../../../lib/generated/adminApi'
import { Button, Drawer, DrawerFootNote, DrawerSection, Status } from '../../../../components/console'
import c from '../../../../components/console/Console.module.css'
import { fmtDuration } from '../opsLabels'
import { OpsQueryError } from '../OpsShared'
import { LOST_HINT, jobFailed, logWindowFor } from './jobs'
import styles from './Exec.module.css'
import { t } from '@/i18n'

/** 狀態：執行中、成功、失敗（Result 不是 success）、結果不明（沒觀測到結束）。 */
export function JobStatus({ job }: { job: JobItem }) {
  if (job.state === 'running') return <Status tone="info">{t('admin.ops.exec.jobDrawer.running')}</Status>
  if (job.state === 'lost') return <span title={LOST_HINT}><Status tone="idle">{t('admin.ops.exec.jobDrawer.resultUnknown')}</Status></span>
  return jobFailed(job) ? <Status tone="bad">{t('admin.ops.exec.jobDrawer.failed')}</Status> : <Status tone="ok">{t('admin.ops.exec.jobDrawer.succeeded')}</Status>
}

/** 精確到秒：排程工作常常幾秒就跑完，分鐘粒度看不出先後。 */
function fmtDateTimeSec(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} `
    + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** 清單裡沒有那一筆、改用網址的 unit 與開始時間另外查時的狀態（查到了就直接給 `job`）。 */
export type JobLookup = { status: 'pending' } | { status: 'error'; error: unknown } | { status: 'missing' }

interface Props {
  /** 要顯示的那一次執行（清單裡的，或另外查到的）；`null`＝沒有可顯示的，原因看 `lookup`。 */
  job: JobItem | null
  /** 沒給＝沒有另外查（舊格式的連結只有 id，或 unit／開始時間的格式不對）。 */
  lookup?: JobLookup
  /** 清單的查詢時間（回應的 `until`），用來換算這次執行距今多久。 */
  now: string
  onClose: () => void
}

/**
 * 單次執行的詳情抽屜（`?job=<invocation_id>&unit=…&at=…`）：這次執行的所有欄位，附「看日誌」連到日誌頁。
 * 這一頁的清單裡沒有那一筆時，頁面會用 unit 與開始時間另外查；查詢中、查詢失敗、查不到各有說明。
 */
export function JobDrawer({ job, lookup, now, onClose }: Props) {
  if (!job && lookup?.status === 'pending') {
    return (
      <Drawer open onClose={onClose} title={t('admin.ops.exec.jobDrawer.loading')} width={520}>
        <DrawerSection><p className={styles.note}>{t('admin.ops.exec.jobDrawer.lookingUpThisRun')}</p></DrawerSection>
      </Drawer>
    )
  }
  if (!job && lookup?.status === 'error') {
    return (
      <Drawer open onClose={onClose} title={t('admin.ops.exec.jobDrawer.couldnTLoadThisRun')} width={520}>
        <DrawerSection><OpsQueryError error={lookup.error} what={t('admin.ops.exec.jobDrawer.thisRun')} /></DrawerSection>
      </Drawer>
    )
  }
  if (!job) {
    return (
      <Drawer open onClose={onClose} title={t('admin.ops.exec.jobDrawer.runNotFound')} width={520}>
        <DrawerSection>
          <p className={styles.note}>
            {lookup
              ? t('admin.ops.exec.jobDrawer.thisRunCanTBe')
              : t('admin.ops.exec.jobDrawer.thisRunIsnTIn')}
          </p>
        </DrawerSection>
      </Drawer>
    )
  }
  const win = logWindowFor(job.started_at, now)
  const footer = job.service ? (
    <>
      <DrawerFootNote>{win.covered ? t('admin.ops.exec.jobDrawer.logsAreReadThroughThe') : t('admin.ops.exec.jobDrawer.theLogsPageLooksBack')}</DrawerFootNote>
      <Link className={c.btn} to={`/admin/operations/logs?service=${encodeURIComponent(job.service)}&since=${win.since}`}>
        {t('admin.ops.exec.jobDrawer.viewLogs')}
      </Link>
    </>
  ) : (
    <Button disabled title={t('admin.ops.exec.jobDrawer.thisUnitIsnTIn')}>{t('admin.ops.exec.jobDrawer.viewLogs')}</Button>
  )
  return (
    <Drawer open onClose={onClose} title={job.service ?? job.unit} monoTitle status={<JobStatus job={job} />}
      subtitle={<span className={c.mono}>{job.unit}</span>} width={520} footer={footer}>
      <DrawerSection title={t('admin.ops.exec.jobDrawer.run')}>
        {job.state === 'lost' && <p className={styles.note}>{t('admin.ops.exec.jobDrawer.text', { LOST_HINT })}</p>}
        <dl className={`${c.kv} ${styles.kvWide}`} aria-label={t('admin.ops.exec.jobDrawer.run')}>
          <dt>{t('admin.ops.exec.jobDrawer.started')}</dt><dd>{fmtDateTimeSec(job.started_at)}</dd>
          <dt>{t('admin.ops.exec.jobDrawer.ended')}</dt><dd>{fmtDateTimeSec(job.finished_at)}</dd>
          <dt>{t('admin.ops.exec.jobDrawer.duration')}</dt><dd>{fmtDuration(job.duration_seconds)}</dd>
          <dt>{t('admin.ops.exec.jobDrawer.lastObserved')}</dt><dd>{fmtDateTimeSec(job.last_seen_at)}</dd>
        </dl>
      </DrawerSection>
      <DrawerSection title={t('admin.ops.exec.jobDrawer.result')}>
        <dl className={`${c.kv} ${styles.kvWide}`} aria-label={t('admin.ops.exec.jobDrawer.result')}>
          <dt>Result</dt><dd>{job.result ?? '—'}</dd>
          <dt>{t('admin.ops.exec.jobDrawer.exitCode')}</dt><dd>{job.exit_status ?? '—'}</dd>
          <dt>{t('admin.ops.exec.jobDrawer.exitType')}</dt><dd>{job.exec_main_code ?? '—'}</dd>
        </dl>
      </DrawerSection>
      <DrawerSection title={t('admin.ops.exec.jobDrawer.identifiers')}>
        <dl className={`${c.kv} ${styles.kvWide}`} aria-label={t('admin.ops.exec.jobDrawer.identifiers')}>
          <dt>{t('admin.ops.exec.jobDrawer.host')}</dt><dd>{job.host}</dd>
          <dt>{t('admin.ops.exec.jobDrawer.service')}</dt><dd>{job.service ?? '—'}</dd>
          <dt>unit</dt><dd>{job.unit}</dd>
          <dt>invocation ID</dt><dd>{job.invocation_id}</dd>
        </dl>
      </DrawerSection>
    </Drawer>
  )
}
