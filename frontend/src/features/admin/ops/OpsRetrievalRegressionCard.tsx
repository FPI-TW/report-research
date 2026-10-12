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

type Reason = NonNullable<RetrievalRegressionResponse['reason']>
type Verdict = NonNullable<RegressionComparison['summary']>['verdict']

// 與 app/services/retrieval_regression.py 的 REASON_* 一致。
const REASON_LABELS: Record<Reason, string> = {
  db_unavailable: 'DB 連不上', low_memory: '可用記憶體不足', sync_running: 'sync 正在跑',
  no_baseline: '還沒有基準', baseline_invalid: '基準檔不能用', incomparable: '基準與現況不可比',
  dataset_invalid: '題集讀不了', embed_failed: '嵌入模型失敗', query_failed: 'DB 查詢失敗', unexpected: '未預期的錯誤',
}
const VERDICT_LABELS: Record<Verdict, string> = { ok: '沒有劣化', degraded: '劣化', incomparable: '不可比（基準太舊）' }
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
      <div className={styles.listTitle}>{title}（{total} 篇{total > refs.length && `，列出前 ${refs.length} 篇`}）</div>
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
  const state = !q.comparable ? '不可比' : q.degraded ? '劣化' : '正常'
  const tone = !q.comparable ? 'idle' : q.degraded ? 'bad' : 'ok'
  const changed = q.lost.length > 0 || q.gained.length > 0
  return (
    <tr>
      <td className={c.num}>{q.id}</td>
      <td>
        <div className={styles.cellText}>{q.question}</div>
        {changed && (
          <details className={styles.details}>
            <summary>流失 {q.lost_total}、新進 {q.gained_total} 篇</summary>
            <ReportList title="基準有、現在不在前段的研報" refs={q.lost} total={q.lost_total} />
            <ReportList title="基準沒有、現在進前段的舊研報" refs={q.gained} total={q.gained_total} />
          </details>
        )}
      </td>
      <td className={`${c.num} ${c.tdRight}`}>{pct(q.report_recall)}</td>
      <td className={`${c.num} ${c.tdRight}`}>{pct(q.raw_report_recall)}</td>
      <td className={`${c.num} ${c.tdRight}`}>{pct(q.chunk_recall)}</td>
      <td className={`${c.num} ${c.tdRight}`}>{num(q.rbo)}</td>
      <td className={`${c.num} ${c.tdRight}`}>{q.excluded_new_reports}</td>
      <td className={`${c.num} ${c.tdRight}`}>{q.hidden_reports}／{q.removed_reports}</td>
      <td>
        <Status tone={tone}>{state}</Status>
        {q.lex_truncated && <div className={c.sub}>字面路截斷</div>}
      </td>
    </tr>
  )
}

function Summary({ c: cmp }: { c: RegressionComparison }) {
  const s = cmp.summary
  const t = cmp.thresholds
  const b = cmp.baseline
  return (
    <dl className={`${c.kv} ${styles.kvWide} ${styles.kvText}`} aria-label="檢索回歸摘要">
      <dt>比對時間</dt>
      <dd>{fmtDateTime(cmp.finished_at)}{cmp.duration_s != null && `（${Math.round(cmp.duration_s)} 秒）`}</dd>
      {s && (
        <>
          <dt>結論</dt>
          <dd>{VERDICT_LABELS[s.verdict]}</dd>
          <dt>平均研報召回</dt>
          <dd>{pct(s.mean_report_recall)}{t && `（門檻 ${pct(t.min_mean_recall)}）`}</dd>
          <dt>崩掉的題數</dt>
          <dd>
            {s.degraded_questions}
            {t && `（單題研報召回低於 ${pct(t.min_question_recall)}；超過 ${t.max_degraded_questions} 題即告警）`}
          </dd>
          <dt>可比較題數</dt>
          <dd>{s.comparable}／{s.questions}</dd>
          <dt>不排除新研報時</dt>
          <dd>{pct(s.mean_raw_report_recall)}（只供參考：語料實際換了多少）</dd>
          <dt>片段召回／RBO</dt>
          <dd>{pct(s.mean_chunk_recall)}／{num(s.mean_rbo)}（只顯示，不判定）</dd>
          <dt>不算劣化的變化</dt>
          <dd>排除新研報 {s.excluded_new_reports}、基準研報被隱藏 {s.hidden_reports}、已下架 {s.removed_reports}</dd>
          {(s.lex_truncated_questions ?? 0) > 0 && (
            <>
              <dt>字面路截斷</dt>
              <dd>{s.lex_truncated_questions} 題（候選超過上限，這幾題每次跑的結果可能不同；單獨一兩題崩掉多半是這個）</dd>
            </>
          )}
        </>
      )}
      {b && (
        <>
          <dt>基準</dt>
          <dd>
            擷取於 {fmtDateTime(b.captured_at)}，語料截點 {fmtDateTime(b.corpus_cutoff)}（{b.corpus_reports} 篇），
            top-{b.k}、dense_scan {b.dense_scan}{b.simulated_as_of && '（驗證用的模擬截點）'}
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
      <table className={c.table} aria-label="檢索回歸逐題">
        <thead>
          <tr>
            <th>題號</th><th>題目</th><th className={c.tdRight}>研報召回</th><th className={c.tdRight}>不排除新研報</th>
            <th className={c.tdRight}>片段召回</th><th className={c.tdRight}>RBO</th>
            <th className={c.tdRight}>排除新研報</th><th className={c.tdRight}>隱藏／下架</th><th>狀態</th>
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
    <Card title="檢索回歸" labelledBy="dh-rr-title" right={q.data && <HealthStatus status={q.data.status} />}>
      <div className={styles.body}>
        {q.isPending && <p className={styles.idle}>載入中…</p>}
        {q.isError && (
          <p className={c.error}>
            檢索回歸載入失敗：{q.error instanceof Error && q.error.message ? q.error.message : '請重試'}
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
        {d.unavailable_reason === 'missing' ? '還沒有結果檔' : `結果檔無法讀取（${d.unavailable_reason ?? ''}）`}。
        {SCHEDULE}；第一次啟用前要先在主機上擷取基準（<code className={c.mono}>scripts/retrieval_regression.py capture</code>）。
      </p>
    )
  }
  const reason = d.reason ? REASON_LABELS[d.reason] : null
  const cmp = d.comparison
  return (
    <>
      <p className={styles.meta}>
        <span>最後一次執行 <b>{fmtDateTime(d.finished_at)}</b></span>
        {d.exit_code != null && <span>退出碼 <b>{d.exit_code}</b></span>}
        <span>{SCHEDULE}</span>
      </p>
      {d.outcome === 'skipped' && (
        <p className={styles.note}>最近一次略過：{reason ?? '原因不明'}（不告警）。下面是上一次真的比對的結果。</p>
      )}
      {d.outcome === 'error' && (
        <p className={c.error}>無法比對（{reason ?? '原因不明'}）：{d.message}</p>
      )}
      {d.outcome === 'degraded' && d.message && <p className={c.error}>{d.message}</p>}
      {d.stale && (
        <p className={c.banner} data-tone="warn">超過 48 小時沒有真的比對過：確認 timer 有在跑、或連續被略過的原因。</p>
      )}
      {cmp ? <Summary c={cmp} /> : <p className={styles.note}>還沒有任何一次比對結果。</p>}
      {cmp?.params_changed && cmp.baseline && (
        <p className={c.banner} data-tone="warn">
          問答的 dense_scan 現在是 {cmp.dense_scan}，基準擷取時是 {cmp.baseline.dense_scan}：比的是現在的設定。確認是刻意調整後，重新擷取基準。
        </p>
      )}
    </>
  )
}
