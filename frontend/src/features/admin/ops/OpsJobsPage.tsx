import { useState } from 'react'
import { adminCsvUrls, type JobItem } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { ExportCsvButton } from '../ExportCsvButton'
import { OpsQueryError } from './OpsShared'
import { fmtDuration } from './opsLabels'
import { JOBS_PAGE_SIZE, useOpsJobs } from './useOps'
import adminStyles from '../Admin.module.css'
import styles from './Ops.module.css'

type JobState = JobItem['state']

const STATE_OPTIONS: { value: JobState | ''; label: string }[] = [
  { value: '', label: '全部' },
  { value: 'running', label: '執行中' },
  { value: 'finished', label: '已結束' },
  { value: 'lost', label: '結果不明' },
]

/** 狀態膠囊：執行中、成功、失敗（Result 不是 success）、結果不明（沒觀測到結束）。 */
function JobStateBadge({ job }: { job: JobItem }) {
  if (job.state === 'running') return <span className={`${styles.pill} ${styles.sTransitioning}`}>執行中</span>
  if (job.state === 'lost') {
    return (
      <span className={`${styles.pill} ${styles.sIdle}`} title="沒觀測到結束，之後已有新一輪開始（收集器停機或兩次觀測之間跑完）">
        結果不明
      </span>
    )
  }
  return job.result === 'success'
    ? <span className={`${styles.pill} ${styles.sRunning}`}>成功</span>
    : <span className={`${styles.pill} ${styles.sFailed}`}>失敗</span>
}

function resultDetail(job: JobItem): string {
  if (job.state !== 'finished') return '—'
  const parts = [job.result ?? '—']
  if (job.exit_status != null && job.exit_status !== 0) parts.push(`exit ${job.exit_status}`)
  if (job.exec_main_code && job.exec_main_code !== 'exited') parts.push(job.exec_main_code)
  return parts.join('・')
}

/**
 * 排程工作：有 timer 的 oneshot 每次執行（新→舊）。資料來自 DB 投影（收集器寫 spool、loader 每 5 分鐘匯入），
 * 不經維運代理，所以代理停掉時這頁照常；但最多晚一輪匯入。「跑過」比照 verify_oneshot_ran.sh。
 */
export default function OpsJobsPage() {
  const [state, setState] = useState<JobState | ''>('')
  const [offset, setOffset] = useState(0)
  const q = useOpsJobs(state, offset)

  return (
    <section className={adminStyles.card} aria-labelledby="ops-jobs-title">
      <div className={adminStyles.cardHead}>
        <h2 id="ops-jobs-title" className={adminStyles.ctitle}>排程工作</h2>
        <ExportCsvButton href={adminCsvUrls.exportJobs({ state: state || undefined })} what="排程工作" />
      </div>
      <div className={adminStyles.form}>
        <label className={adminStyles.field}>
          狀態
          <select
            value={state}
            onChange={e => { setState(e.target.value as JobState | ''); setOffset(0) }}
          >
            {STATE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
      </div>
      <div className={adminStyles.spacer} />
      {q.isPending ? (
        <p className={adminStyles.idle}>載入中…</p>
      ) : q.isError ? (
        <OpsQueryError error={q.error} what="排程工作" />
      ) : (
        <>
          <p className={styles.meta}>
            <span>範圍 <b>{fmtDateTime(q.data.since)}</b> ～ <b>{fmtDateTime(q.data.until)}</b></span>
            <span>共 <b>{q.data.total}</b> 筆（每 5 分鐘匯入一次）</span>
          </p>
          <div className={adminStyles.spacer} />
          {q.data.items.length === 0 ? (
            <p className={adminStyles.idle}>
              這段期間沒有執行紀錄。若一直是空的，確認主機上的收集器（report-mark-metrics）與匯入器
              （report-mark-load-observations.timer）都在跑。
            </p>
          ) : (
            <div className={adminStyles.tableWrap}>
              <table className={adminStyles.table} aria-label="排程工作執行紀錄">
                <thead>
                  <tr><th>服務</th><th>狀態</th><th>開始</th><th>結束</th><th>耗時</th><th>Result</th></tr>
                </thead>
                <tbody>
                  {q.data.items.map(job => (
                    <tr key={`${job.host}/${job.unit}/${job.invocation_id}`}>
                      <td className={adminStyles.wrapCell}>
                        {job.service ?? job.unit}
                        <div className={adminStyles.muted}>{job.unit}</div>
                      </td>
                      <td><JobStateBadge job={job} /></td>
                      <td className={adminStyles.num}>{fmtDateTime(job.started_at)}</td>
                      <td className={adminStyles.num}>{fmtDateTime(job.finished_at)}</td>
                      <td className={adminStyles.num}>{fmtDuration(job.duration_seconds)}</td>
                      <td>{resultDetail(job)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {(offset > 0 || q.data.has_more) && (
            <div className={adminStyles.pager}>
              <button
                type="button"
                className={adminStyles.action}
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - JOBS_PAGE_SIZE))}
              >
                較新
              </button>
              <span>第 {offset + 1}–{offset + q.data.items.length} 筆</span>
              <button
                type="button"
                className={adminStyles.action}
                disabled={!q.data.has_more}
                onClick={() => setOffset(q.data.next_offset ?? offset)}
              >
                較舊
              </button>
            </div>
          )}
        </>
      )}
    </section>
  )
}
