import { useState } from 'react'
import { adminCsvUrls, type JobItem } from '../../../lib/generated/adminApi'
import { Button, Card, PageHeader, Segmented, useQueryParam, type SegOption } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { fmtDateTime } from '../auditLabels'
import { ExportCsvButton } from '../ExportCsvButton'
import { JobDrawer, JobStatus } from './exec/JobDrawer'
import { jobFailed } from './exec/jobs'
import { OpsQueryError } from './OpsShared'
import { fmtDuration } from './opsLabels'
import { JOBS_PAGE_SIZE, useOpsJobs } from './useOps'

type JobState = JobItem['state']

const STATE_OPTIONS: readonly SegOption<JobState | ''>[] = [
  { value: '', label: '全部' },
  { value: 'running', label: '執行中' },
  { value: 'finished', label: '已結束' },
  { value: 'lost', label: '結果不明' },
]

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
 * 點列開詳情抽屜，開著哪一筆放在網址（`?job=<invocation_id>`）。
 */
export default function OpsJobsPage() {
  const [state, setState] = useState<JobState | ''>('')
  const [offset, setOffset] = useState(0)
  const [jobId, setJobId] = useQueryParam('job')
  const q = useOpsJobs(state, offset)

  return (
    <>
      <PageHeader
        summary="有 timer 的 oneshot 每次執行的紀錄，新的在前；最多晚一輪匯入。"
        help={(
          <>
            資料來自 DB 投影（主機上的收集器寫 spool、匯入器每 5 分鐘匯入一次），不經維運代理，所以代理停掉時這頁照常。
            「失敗」＝已結束但 Result 不是 success；「結果不明」＝沒觀測到結束、之後已有新一輪開始。預設列出最近 7 天。
          </>
        )}
        actions={<ExportCsvButton href={adminCsvUrls.exportJobs({ state: state || undefined })} what="排程工作" />}
      />
      <div className={c.toolbar}>
        <Segmented label="狀態" options={STATE_OPTIONS} value={state} onChange={v => { setState(v); setOffset(0) }} />
      </div>
      {q.isPending ? (
        <p className={c.empty}>載入中…</p>
      ) : q.isError ? (
        <OpsQueryError error={q.error} what="排程工作" />
      ) : (
        <>
          <Card
            title="執行紀錄"
            labelledBy="ops-jobs-title"
            right={(
              <p className={c.meta}>
                <span>範圍 <b>{fmtDateTime(q.data.since)}</b> ～ <b>{fmtDateTime(q.data.until)}</b></span>
                <span>共 <b>{q.data.total}</b> 筆（每 5 分鐘匯入一次）</span>
              </p>
            )}
          >
            {q.data.items.length === 0 ? (
              <p className={c.empty}>
                這段期間沒有執行紀錄。若一直是空的，確認主機上的收集器（report-mark-metrics）與匯入器
                （report-mark-load-observations.timer）都在跑。
              </p>
            ) : (
              <div className={c.tableWrap}>
                <table className={c.table} aria-label="排程工作執行紀錄">
                  <thead>
                    <tr><th>服務</th><th>狀態</th><th>開始</th><th>結束</th><th>耗時</th><th>Result</th></tr>
                  </thead>
                  <tbody>
                    {q.data.items.map(job => {
                      const name = job.service ?? job.unit
                      return (
                        <tr
                          key={`${job.host}/${job.unit}/${job.invocation_id}`}
                          className={`${c.rowClickable} ${jobFailed(job) ? c.rowBad : ''}`}
                          onClick={() => setJobId(job.invocation_id)}
                        >
                          <td>
                            <button
                              type="button"
                              className={c.rowLink}
                              title="查看這次執行的詳情"
                              onClick={e => { e.stopPropagation(); setJobId(job.invocation_id) }}
                            >
                              <span className={c.mono}>{name}</span>
                            </button>
                            <div className={`${c.sub} ${c.mono}`}>{job.unit}</div>
                          </td>
                          <td><JobStatus job={job} /></td>
                          <td className={c.num}>{fmtDateTime(job.started_at)}</td>
                          <td className={c.num}>{fmtDateTime(job.finished_at)}</td>
                          <td className={c.num}>{fmtDuration(job.duration_seconds)}</td>
                          <td className={c.mono}>{resultDetail(job)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
            {(offset > 0 || q.data.has_more) && (
              <div className={c.pager}>
                <span>第 {offset + 1}–{offset + q.data.items.length} 筆</span>
                <div className={c.pagerBtns}>
                  <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - JOBS_PAGE_SIZE))}>
                    較新
                  </Button>
                  <Button size="sm" disabled={!q.data.has_more} onClick={() => setOffset(q.data.next_offset ?? offset)}>
                    較舊
                  </Button>
                </div>
              </div>
            )}
          </Card>
          {jobId && (
            <JobDrawer
              job={q.data.items.find(j => j.invocation_id === jobId) ?? null}
              now={q.data.until}
              onClose={() => setJobId(null)}
            />
          )}
        </>
      )}
    </>
  )
}
