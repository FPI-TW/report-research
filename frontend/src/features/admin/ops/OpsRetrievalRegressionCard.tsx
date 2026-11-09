import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { Card, Status } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import {
  adminApi, type RegressionComparison, type RegressionQuestion, type RegressionReportRef,
  type RetrievalRegressionResponse,
} from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { HealthStatus } from './data/HealthStatus'
import { OPS_KEY } from './useOps'
import styles from './data/Data.module.css'
import { t } from '@/i18n'

type Reason = NonNullable<RetrievalRegressionResponse['reason']>
type Verdict = NonNullable<RegressionComparison['summary']>['verdict']

// 與 app/services/retrieval_regression.py 的 REASON_* 一致。
const REASON_LABELS: Record<Reason, string> = {
  get db_unavailable() { return t('admin.ops.opsRetrievalRegressionCard.dbUnreachable') }, get low_memory() { return t('admin.ops.opsRetrievalRegressionCard.notEnoughAvailableMemory') }, get sync_running() { return t('admin.ops.opsRetrievalRegressionCard.syncIsRunning') },
  get no_baseline() { return t('admin.ops.opsRetrievalRegressionCard.noBaselineYet') }, get baseline_invalid() { return t('admin.ops.opsRetrievalRegressionCard.baselineFileUnusable') }, get incomparable() { return t('admin.ops.opsRetrievalRegressionCard.baselineNotComparableWithCurrent') },
  get dataset_invalid() { return t('admin.ops.opsRetrievalRegressionCard.questionSetUnreadable') }, get embed_failed() { return t('admin.ops.opsRetrievalRegressionCard.embeddingModelFailed') }, get query_failed() { return t('admin.ops.opsRetrievalRegressionCard.dbQueryFailed') }, get unexpected() { return t('admin.ops.opsRetrievalRegressionCard.unexpectedError') },
}
const VERDICT_LABELS: Record<Verdict, string> = { get ok() { return t('admin.ops.opsRetrievalRegressionCard.noRegression') }, get degraded() { return t('admin.ops.opsRetrievalRegressionCard.regressed') }, get incomparable() { return t('admin.ops.opsRetrievalRegressionCard.notComparableBaselineTooOld') } }
const SCHEDULE = '每日 07:40 由 report-mark-retrieval-regression 執行（零 LLM；web 不跑檢索）'

function pct(v: number | null | undefined): string {
  return v == null ? '—' : `${Math.round(v * 100)}%`
}

function num(v: number | null | undefined): string {
  return v == null ? '—' : v.toFixed(2)
}

function useRetrievalRegression() {
  return useQuery<RetrievalRegressionResponse>({
    queryKey: [...OPS_KEY, 'retrieval-regression'],
    queryFn: () => adminApi.getRetrievalRegression(),
    retry: false,
  })
}

function ReportList({ title, refs, total }: { title: string; refs: RegressionReportRef[]; total: number }) {
  if (refs.length === 0) return null
  return (
    <div>
      <div className={styles.listTitle}>{t('admin.ops.opsRetrievalRegressionCard.reports', { title, total })}{total > refs.length && t('admin.ops.opsRetrievalRegressionCard.showingTheFirst', { length: refs.length })}{t('admin.ops.opsRetrievalRegressionCard.text')}</div>
      <ul className={styles.list}>
        {refs.map(r => (
          <li key={r.file_hash}>
            <Link className={c.link} to={`/report/${r.file_hash}`}>{r.label ?? `${r.file_hash.slice(0, 12)}…`}</Link>
          </li>
        ))}
      </ul>
    </div>
  )
}

function QuestionRow({ q }: { q: RegressionQuestion }) {
  const state = !q.comparable ? t('admin.ops.opsRetrievalRegressionCard.notComparable') : q.degraded ? t('admin.ops.opsRetrievalRegressionCard.regressed') : t('admin.ops.opsRetrievalRegressionCard.ok')
  const tone = !q.comparable ? 'idle' : q.degraded ? 'bad' : 'ok'
  const changed = q.lost.length > 0 || q.gained.length > 0
  return (
    <tr>
      <td className={c.num}>{q.id}</td>
      <td>
        <div className={styles.cellText}>{q.question}</div>
        {changed && (
          <details className={styles.details}>
            <summary>{t('admin.ops.opsRetrievalRegressionCard.lostGained', { lost_total: q.lost_total, gained_total: q.gained_total })}</summary>
            <ReportList title={t('admin.ops.opsRetrievalRegressionCard.reportsInTheBaselineNo')} refs={q.lost} total={q.lost_total} />
            <ReportList title={t('admin.ops.opsRetrievalRegressionCard.olderReportsNotInThe')} refs={q.gained} total={q.gained_total} />
          </details>
        )}
      </td>
      <td className={`${c.num} ${c.tdRight}`}>{pct(q.report_recall)}</td>
      <td className={`${c.num} ${c.tdRight}`}>{pct(q.raw_report_recall)}</td>
      <td className={`${c.num} ${c.tdRight}`}>{pct(q.chunk_recall)}</td>
      <td className={`${c.num} ${c.tdRight}`}>{num(q.rbo)}</td>
      <td className={`${c.num} ${c.tdRight}`}>{q.excluded_new_reports}</td>
      <td className={`${c.num} ${c.tdRight}`}>{t('admin.ops.opsRetrievalRegressionCard.text2', { hidden_reports: q.hidden_reports, removed_reports: q.removed_reports })}</td>
      <td>
        <Status tone={tone}>{state}</Status>
        {q.lex_truncated && <div className={c.sub}>{t('admin.ops.opsRetrievalRegressionCard.lexicalPathTruncated')}</div>}
      </td>
    </tr>
  )
}

function Summary({ c: cmp }: { c: RegressionComparison }) {
  const s = cmp.summary
  const item = cmp.thresholds
  const b = cmp.baseline
  return (
    <dl className={`${c.kv} ${styles.kvWide} ${styles.kvText}`} aria-label={t('admin.ops.opsRetrievalRegressionCard.retrievalRegressionSummary')}>
      <dt>{t('admin.ops.opsRetrievalRegressionCard.comparedAt')}</dt>
      <dd>{fmtDateTime(cmp.finished_at)}{cmp.duration_s != null && t('admin.ops.opsRetrievalRegressionCard.s', { duration_s: Math.round(cmp.duration_s) })}</dd>
      {s && (
        <>
          <dt>{t('admin.ops.opsRetrievalRegressionCard.verdict')}</dt>
          <dd>{VERDICT_LABELS[s.verdict]}</dd>
          <dt>{t('admin.ops.opsRetrievalRegressionCard.meanReportRecall')}</dt>
          <dd>{pct(s.mean_report_recall)}{item && t('admin.ops.opsRetrievalRegressionCard.threshold', { min_mean_recall: pct(item.min_mean_recall) })}</dd>
          <dt>{t('admin.ops.opsRetrievalRegressionCard.collapsedQuestions')}</dt>
          <dd>
            {s.degraded_questions}
            {item && t('admin.ops.opsRetrievalRegressionCard.perQuestionReportRecallBelow', { min_question_recall: pct(item.min_question_recall), max_degraded_questions: item.max_degraded_questions })}
          </dd>
          <dt>{t('admin.ops.opsRetrievalRegressionCard.comparableQuestions')}</dt>
          <dd>{t('admin.ops.opsRetrievalRegressionCard.text3', { comparable: s.comparable, questions: s.questions })}</dd>
          <dt>{t('admin.ops.opsRetrievalRegressionCard.withoutExcludingNewReports')}</dt>
          <dd>{t('admin.ops.opsRetrievalRegressionCard.forReferenceOnlyHowMuch', { mean_raw_report_recall: pct(s.mean_raw_report_recall) })}</dd>
          <dt>{t('admin.ops.opsRetrievalRegressionCard.chunkRecallRbo')}</dt>
          <dd>{t('admin.ops.opsRetrievalRegressionCard.shownOnlyNotJudged', { mean_chunk_recall: pct(s.mean_chunk_recall), mean_rbo: num(s.mean_rbo) })}</dd>
          <dt>{t('admin.ops.opsRetrievalRegressionCard.changesNotCountedAsRegression')}</dt>
          <dd>{t('admin.ops.opsRetrievalRegressionCard.excludedNewReportsBaselineReports', { excluded_new_reports: s.excluded_new_reports, hidden_reports: s.hidden_reports, removed_reports: s.removed_reports })}</dd>
          {(s.lex_truncated_questions ?? 0) > 0 && (
            <>
              <dt>{t('admin.ops.opsRetrievalRegressionCard.lexicalPathTruncated')}</dt>
              <dd>{s.lex_truncated_questions}{' '}{t('admin.ops.opsRetrievalRegressionCard.questionsCandidatesOverTheLimit')}</dd>
            </>
          )}
        </>
      )}
      {b && (
        <>
          <dt>{t('admin.ops.opsRetrievalRegressionCard.baseline')}</dt>
          <dd>
            {t('admin.ops.opsRetrievalRegressionCard.capturedAtCorpusCutoffReports', { captured_at: fmtDateTime(b.captured_at), corpus_cutoff: fmtDateTime(b.corpus_cutoff), corpus_reports: b.corpus_reports, k: b.k, dense_scan: b.dense_scan })}{b.simulated_as_of && t('admin.ops.opsRetrievalRegressionCard.simulatedCutoffForValidation')}
          </dd>
        </>
      )}
    </dl>
  )
}

function Questions({ questions }: { questions: RegressionQuestion[] }) {
  if (questions.length === 0) return null
  return (
    <div className={c.tableWrap}>
      <table className={c.table} aria-label={t('admin.ops.opsRetrievalRegressionCard.retrievalRegressionByQuestion')}>
        <thead>
          <tr>
            <th>{t('admin.ops.opsRetrievalRegressionCard.no')}</th><th>{t('admin.ops.opsRetrievalRegressionCard.question')}</th><th className={c.tdRight}>{t('admin.ops.opsRetrievalRegressionCard.reportRecall')}</th><th className={c.tdRight}>{t('admin.ops.opsRetrievalRegressionCard.withoutExcludingNew')}</th>
            <th className={c.tdRight}>{t('admin.ops.opsRetrievalRegressionCard.chunkRecall')}</th><th className={c.tdRight}>RBO</th>
            <th className={c.tdRight}>{t('admin.ops.opsRetrievalRegressionCard.newExcluded')}</th><th className={c.tdRight}>{t('admin.ops.opsRetrievalRegressionCard.hiddenRemoved')}</th><th>{t('admin.ops.opsRetrievalRegressionCard.status')}</th>
          </tr>
        </thead>
        <tbody>
          {questions.map(q => <QuestionRow key={q.id} q={q} />)}
        </tbody>
      </table>
    </div>
  )
}

/**
 * 檢索回歸（`GET /api/admin/retrieval-regression`）：凍結題集的 top-k 對一次性基準。只讀 timer 最後一次的結果檔；
 * 略過或錯誤的那次不蓋掉上一次的比對。放在資料健康頁，自己的查詢失敗不影響上面三段。
 */
export default function OpsRetrievalRegressionCard() {
  const q = useRetrievalRegression()
  return (
    <Card title={t('admin.ops.opsRetrievalRegressionCard.retrievalRegression')} labelledBy="dh-rr-title" right={q.data && <HealthStatus status={q.data.status} />}>
      <div className={styles.body}>
        {q.isPending && <p className={styles.idle}>{t('admin.ops.opsRetrievalRegressionCard.loading')}</p>}
        {q.isError && (
          <p className={c.error}>
            {t('admin.ops.opsRetrievalRegressionCard.couldnTLoadRetrievalRegression')}{q.error instanceof Error && q.error.message ? q.error.message : t('admin.ops.opsRetrievalRegressionCard.tryAgain')}
          </p>
        )}
        {q.data && <Body d={q.data} />}
      </div>
      {q.data?.available && q.data.comparison && <Questions questions={q.data.comparison.questions} />}
    </Card>
  )
}

function Body({ d }: { d: RetrievalRegressionResponse }) {
  if (!d.available) {
    return (
      <p className={styles.idle}>
        {d.unavailable_reason === 'missing' ? t('admin.ops.opsRetrievalRegressionCard.noResultFileYet') : t('admin.ops.opsRetrievalRegressionCard.resultFileUnreadable', { v: d.unavailable_reason ?? '' })}{t('admin.ops.opsRetrievalRegressionCard.captureABaselineOnThe', { SCHEDULE })}<code className={c.mono}>scripts/retrieval_regression.py capture</code>{t('admin.ops.opsRetrievalRegressionCard.text4')}
      </p>
    )
  }
  const reason = d.reason ? REASON_LABELS[d.reason] : null
  const cmp = d.comparison
  return (
    <>
      <p className={c.meta}>
        <span>{t('admin.ops.opsRetrievalRegressionCard.lastRun')}{' '}<b>{fmtDateTime(d.finished_at)}</b></span>
        {d.exit_code != null && <span>{t('admin.ops.opsRetrievalRegressionCard.exitCode')}{' '}<b>{d.exit_code}</b></span>}
        <span>{SCHEDULE}</span>
      </p>
      {d.outcome === 'skipped' && (
        <p className={styles.note}>{t('admin.ops.opsRetrievalRegressionCard.lastSkipped')}{reason ?? t('admin.ops.opsRetrievalRegressionCard.unknownReason')}{t('admin.ops.opsRetrievalRegressionCard.noAlertBelowIsThe')}</p>
      )}
      {d.outcome === 'error' && (
        <p className={c.error}>{t('admin.ops.opsRetrievalRegressionCard.couldnTCompare')}{reason ?? t('admin.ops.opsRetrievalRegressionCard.unknownReason')}{t('admin.ops.opsRetrievalRegressionCard.text5')}{d.message}</p>
      )}
      {d.outcome === 'degraded' && d.message && <p className={c.error}>{d.message}</p>}
      {d.stale && (
        <p className={c.banner} data-tone="warn">{t('admin.ops.opsRetrievalRegressionCard.noRealComparisonForOver')}</p>
      )}
      {cmp ? <Summary c={cmp} /> : <p className={styles.note}>{t('admin.ops.opsRetrievalRegressionCard.noComparisonResultsYet')}</p>}
      {cmp?.params_changed && cmp.baseline && (
        <p className={c.banner} data-tone="warn">
          {t('admin.ops.opsRetrievalRegressionCard.qADenseScanIs')}{' '}{cmp.dense_scan}{t('admin.ops.opsRetrievalRegressionCard.butItWasWhenThe', { dense_scan: cmp.baseline.dense_scan })}
        </p>
      )}
    </>
  )
}
