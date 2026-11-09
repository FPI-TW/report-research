import { useState } from 'react'
import { useSearchParams } from 'react-router'
import { adminCsvUrls, type JobItem } from '../../../lib/generated/adminApi'
import { Button, Card, PageHeader, Segmented, type SegOption } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { fmtDateTime } from '../auditLabels'
import { ExportCsvButton } from '../ExportCsvButton'
import { JobDrawer, JobStatus, type JobLookup } from './exec/JobDrawer'
import { jobFailed, jobLocator } from './exec/jobs'
import { OpsQueryError } from './OpsShared'
import { fmtDuration } from './opsLabels'
import { JOBS_PAGE_SIZE, useOpsJobLookup, useOpsJobs } from './useOps'
import { t } from '@/i18n'

type JobState = JobItem['state']

const STATE_OPTIONS: readonly SegOption<JobState | ''>[] = [
  { value: '', get label() { return t('admin.ops.opsJobsPage.all') } },
  { value: 'running', get label() { return t('admin.ops.opsJobsPage.running') } },
  { value: 'finished', get label() { return t('admin.ops.opsJobsPage.finished') } },
  { value: 'lost', get label() { return t('admin.ops.opsJobsPage.resultUnknown') } },
]

/** 抽屜用到的網址參數：開哪一筆（job），以及分享連結定位用的 unit 與開始時間（at）。 */
const JOB_PARAMS = ['job', 'unit', 'at'] as const

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
 * 點列開詳情抽屜，開著哪一筆放在網址（`?job=<invocation_id>&unit=<unit>&at=<開始時間>`）：後兩個是給分享出去的
 * 連結定位用的——對方的清單不含那一筆（別的篩選、別的頁、被新紀錄推走）時，用它們另外查出那一筆。
 */
export default function OpsJobsPage() {
  const [state, setState] = useState<JobState | ''>('')
  const [offset, setOffset] = useState(0)
  const [params, setParams] = useSearchParams()
  const jobId = params.get('job')
  const q = useOpsJobs(state, offset)
  // 打開多一筆瀏覽紀錄（按上一頁就關），關閉則取代目前這一筆；定位用的 unit、at 跟著 job 一起加、一起拿掉。
  const openJob = (job: JobItem) => setParams(prev => {
    const p = new URLSearchParams(prev)
    p.set('job', job.invocation_id)
    p.set('unit', job.unit)
    p.set('at', job.started_at)
    return p
  })
  const closeJob = () => setParams(prev => {
    const p = new URLSearchParams(prev)
    for (const key of JOB_PARAMS) p.delete(key)
    return p
  }, { replace: true })
  const listed = jobId ? q.data?.items.find(j => j.invocation_id === jobId) ?? null : null
  // 清單載入完、裡面沒有那一筆，而且網址的 unit／at 格式正確，才另外查；不影響清單自己的篩選與分頁。
  const locator = jobId && q.isSuccess && !listed ? jobLocator(params.get('unit'), params.get('at')) : null
  const found = useOpsJobLookup(jobId ?? '', locator)
  const lookup: JobLookup | undefined = !locator ? undefined
    : found.isPending ? { status: 'pending' }
      : found.isError ? { status: 'error', error: found.error }
        : found.data ? undefined : { status: 'missing' }

  return (
    <>
      <PageHeader
        summary={t('admin.ops.opsJobsPage.runsOfTimerDrivenOneshots')}
        help={(
          <>
            {t('admin.ops.opsJobsPage.dataComesFromADb')}
          </>
        )}
        actions={<ExportCsvButton href={adminCsvUrls.exportJobs({ state: state || undefined })} what={t('admin.ops.opsJobsPage.scheduledJobs')} />}
      />
      <div className={c.toolbar}>
        <Segmented label={t('admin.ops.opsJobsPage.status')} options={STATE_OPTIONS} value={state} onChange={v => { setState(v); setOffset(0) }} />
      </div>
      {q.isPending ? (
        <p className={c.empty}>{t('admin.ops.opsJobsPage.loading')}</p>
      ) : q.isError ? (
        <OpsQueryError error={q.error} what={t('admin.ops.opsJobsPage.scheduledJobs')} />
      ) : (
        <>
          <Card
            title={t('admin.ops.opsJobsPage.runHistory')}
            labelledBy="ops-jobs-title"
            right={(
              <p className={c.meta}>
                <span>{t('admin.ops.opsJobsPage.range')}{' '}<b>{fmtDateTime(q.data.since)}</b>{' '}{t('admin.ops.opsJobsPage.text')}{' '}<b>{fmtDateTime(q.data.until)}</b></span>
                <span>{t('admin.ops.opsJobsPage.text2')}{' '}<b>{q.data.total}</b>{' '}{t('admin.ops.opsJobsPage.runsInTotalImportedEvery')}</span>
              </p>
            )}
          >
            {q.data.items.length === 0 ? (
              <p className={c.empty}>
                {t('admin.ops.opsJobsPage.noRunsInThisPeriod')}
              </p>
            ) : (
              <div className={c.tableWrap}>
                <table className={c.table} aria-label={t('admin.ops.opsJobsPage.scheduledJobRuns')}>
                  <thead>
                    <tr><th>{t('admin.ops.opsJobsPage.service')}</th><th>{t('admin.ops.opsJobsPage.status')}</th><th>{t('admin.ops.opsJobsPage.started')}</th><th>{t('admin.ops.opsJobsPage.ended')}</th><th>{t('admin.ops.opsJobsPage.duration')}</th><th>Result</th></tr>
                  </thead>
                  <tbody>
                    {q.data.items.map(job => {
                      const name = job.service ?? job.unit
                      return (
                        <tr
                          key={`${job.host}/${job.unit}/${job.invocation_id}`}
                          className={`${c.rowClickable} ${jobFailed(job) ? c.rowBad : ''}`}
                          onClick={() => openJob(job)}
                        >
                          <td>
                            <button
                              type="button"
                              className={c.rowLink}
                              title={t('admin.ops.opsJobsPage.viewDetailsOfThisRun')}
                              onClick={e => { e.stopPropagation(); openJob(job) }}
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
                <span>{t('admin.ops.opsJobsPage.text3', { v: offset + 1, v2: offset + q.data.items.length })}</span>
                <div className={c.pagerBtns}>
                  <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - JOBS_PAGE_SIZE))}>
                    {t('admin.ops.opsJobsPage.newer')}
                  </Button>
                  <Button size="sm" disabled={!q.data.has_more} onClick={() => setOffset(q.data.next_offset ?? offset)}>
                    {t('admin.ops.opsJobsPage.older')}
                  </Button>
                </div>
              </div>
            )}
          </Card>
          {jobId && (
            <JobDrawer job={listed ?? found.data ?? null} lookup={lookup} now={q.data.until} onClose={closeJob} />
          )}
        </>
      )}
    </>
  )
}
