import { Link } from 'react-router'
import type { JobItem } from '../../../../lib/generated/adminApi'
import { Button, Drawer, DrawerFootNote, DrawerSection, Status } from '../../../../components/console'
import c from '../../../../components/console/Console.module.css'
import { fmtDuration } from '../opsLabels'
import { LOST_HINT, jobFailed, logWindowFor } from './jobs'
import styles from './Exec.module.css'

/** 狀態：執行中、成功、失敗（Result 不是 success）、結果不明（沒觀測到結束）。 */
export function JobStatus({ job }: { job: JobItem }) {
  if (job.state === 'running') return <Status tone="info">執行中</Status>
  if (job.state === 'lost') return <span title={LOST_HINT}><Status tone="idle">結果不明</Status></span>
  return jobFailed(job) ? <Status tone="bad">失敗</Status> : <Status tone="ok">成功</Status>
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

interface Props {
  /** 清單裡找到的那一次執行；`null`＝網址帶了 id 但這一頁的清單裡沒有。 */
  job: JobItem | null
  /** 清單的查詢時間（回應的 `until`），用來換算這次執行距今多久。 */
  now: string
  onClose: () => void
}

/** 單次執行的詳情抽屜（`?job=<invocation_id>`）：這次執行的所有欄位，附「看日誌」連到日誌頁。 */
export function JobDrawer({ job, now, onClose }: Props) {
  if (!job) {
    return (
      <Drawer open onClose={onClose} title="找不到這次執行" width={520}>
        <DrawerSection>
          <p className={styles.note}>這一頁的清單裡沒有這次執行：可能在別的狀態篩選或分頁，或已超出查詢範圍。</p>
        </DrawerSection>
      </Drawer>
    )
  }
  const win = logWindowFor(job.started_at, now)
  const footer = job.service ? (
    <>
      <DrawerFootNote>{win.covered ? '日誌經維運代理讀取' : '日誌頁最多回看 1 天，看不到這次執行'}</DrawerFootNote>
      <Link className={c.btn} to={`/admin/operations/logs?service=${encodeURIComponent(job.service)}&since=${win.since}`}>
        看日誌
      </Link>
    </>
  ) : (
    <Button disabled title="這個 unit 不在 Service Catalog，沒有對應的日誌">看日誌</Button>
  )
  return (
    <Drawer open onClose={onClose} title={job.service ?? job.unit} monoTitle status={<JobStatus job={job} />}
      subtitle={<span className={c.mono}>{job.unit}</span>} width={520} footer={footer}>
      <DrawerSection title="執行">
        {job.state === 'lost' && <p className={styles.note}>{LOST_HINT}。</p>}
        <dl className={`${c.kv} ${styles.kvWide}`} aria-label="執行">
          <dt>開始</dt><dd>{fmtDateTimeSec(job.started_at)}</dd>
          <dt>結束</dt><dd>{fmtDateTimeSec(job.finished_at)}</dd>
          <dt>耗時</dt><dd>{fmtDuration(job.duration_seconds)}</dd>
          <dt>最後觀測</dt><dd>{fmtDateTimeSec(job.last_seen_at)}</dd>
        </dl>
      </DrawerSection>
      <DrawerSection title="結果">
        <dl className={`${c.kv} ${styles.kvWide}`} aria-label="結果">
          <dt>Result</dt><dd>{job.result ?? '—'}</dd>
          <dt>結束碼</dt><dd>{job.exit_status ?? '—'}</dd>
          <dt>結束方式</dt><dd>{job.exec_main_code ?? '—'}</dd>
        </dl>
      </DrawerSection>
      <DrawerSection title="識別">
        <dl className={`${c.kv} ${styles.kvWide}`} aria-label="識別">
          <dt>主機</dt><dd>{job.host}</dd>
          <dt>服務</dt><dd>{job.service ?? '—'}</dd>
          <dt>unit</dt><dd>{job.unit}</dd>
          <dt>invocation ID</dt><dd>{job.invocation_id}</dd>
        </dl>
      </DrawerSection>
    </Drawer>
  )
}
