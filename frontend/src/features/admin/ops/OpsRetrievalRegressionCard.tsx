import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import {
  adminApi, type RegressionComparison, type RegressionQuestion, type RegressionReportRef,
  type RetrievalRegressionResponse,
} from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { OPS_KEY } from './useOps'
import adminStyles from '../Admin.module.css'
import styles from './Ops.module.css'

type Status = RetrievalRegressionResponse['status']
type Reason = NonNullable<RetrievalRegressionResponse['reason']>
type Verdict = NonNullable<RegressionComparison['summary']>['verdict']

const STATUS_LABELS: Record<Status, string> = { ok: '正常', warn: '注意', fail: '異常', unknown: '未知' }
const STATUS_CLASS: Record<Status, string> = {
  ok: styles.sRunning, warn: styles.sTransitioning, fail: styles.sFailed, unknown: styles.sIdle,
}
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
      <div className={adminStyles.muted}>{title}（{total} 篇{total > refs.length && `，列出前 ${refs.length} 篇`}）</div>
      <ul className={styles.problems}>
        {refs.map(r => (
          <li key={r.file_hash}>
            <Link to={`/report/${r.file_hash}`}>{r.label ?? `${r.file_hash.slice(0, 12)}…`}</Link>
          </li>
        ))}
      </ul>
    </div>
  )
}

function QuestionRow({ q }: { q: RegressionQuestion }) {
  const state = !q.comparable ? '不可比' : q.degraded ? '劣化' : '正常'
  const cls = !q.comparable ? styles.sIdle : q.degraded ? styles.sFailed : styles.sRunning
  const changed = q.lost.length > 0 || q.gained.length > 0
  return (
    <tr>
      <td className={adminStyles.num}>{q.id}</td>
      <td>
        <div className={styles.cellText}>{q.question}</div>
        {changed && (
          <details className={styles.journal}>
            <summary>流失 {q.lost_total}、新進 {q.gained_total} 篇</summary>
            <ReportList title="基準有、現在不在前段的研報" refs={q.lost} total={q.lost_total} />
            <ReportList title="基準沒有、現在進前段的舊研報" refs={q.gained} total={q.gained_total} />
          </details>
        )}
      </td>
      <td className={adminStyles.num}>{pct(q.report_recall)}</td>
      <td className={adminStyles.num}>{pct(q.raw_report_recall)}</td>
      <td className={adminStyles.num}>{pct(q.chunk_recall)}</td>
      <td className={adminStyles.num}>{num(q.rbo)}</td>
      <td className={adminStyles.num}>{q.excluded_new_reports}</td>
      <td className={adminStyles.num}>{q.hidden_reports}／{q.removed_reports}</td>
      <td>
        <span className={`${styles.pill} ${cls}`}>{state}</span>
        {q.lex_truncated && <div className={adminStyles.muted}>字面路截斷</div>}
      </td>
    </tr>
  )
}

function Comparison({ c }: { c: RegressionComparison }) {
  const s = c.summary
  const t = c.thresholds
  const b = c.baseline
  return (
    <>
      <div className={adminStyles.spacer} />
      <dl className={styles.dl} aria-label="檢索回歸摘要">
        <dt>比對時間</dt>
        <dd>{fmtDateTime(c.finished_at)}{c.duration_s != null && `（${Math.round(c.duration_s)} 秒）`}</dd>
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
      {c.params_changed && b && (
        <p className={styles.warnNote}>
          問答的 dense_scan 現在是 {c.dense_scan}，基準擷取時是 {b.dense_scan}：比的是現在的設定。確認是刻意調整後，重新擷取基準。
        </p>
      )}
      {c.questions.length > 0 && (
        <>
          <div className={adminStyles.spacer} />
          <div className={adminStyles.tableWrap}>
            <table className={adminStyles.table} aria-label="檢索回歸逐題">
              <thead>
                <tr>
                  <th>題號</th><th>題目</th><th>研報召回</th><th>不排除新研報</th><th>片段召回</th><th>RBO</th>
                  <th>排除新研報</th><th>隱藏／下架</th><th>狀態</th>
                </tr>
              </thead>
              <tbody>
                {c.questions.map(q => <QuestionRow key={q.id} q={q} />)}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  )
}

/**
 * 檢索回歸（`GET /api/admin/retrieval-regression`）：凍結題集的 top-k 對一次性基準。只讀 timer 最後一次的結果檔；
 * 略過或錯誤的那次不蓋掉上一次的比對。放在資料健康頁，自己的查詢失敗不影響上面三段。
 */
export default function OpsRetrievalRegressionCard() {
  const q = useRetrievalRegression()
  return (
    <section className={adminStyles.card} aria-labelledby="dh-rr-title">
      <div className={adminStyles.cardHead}>
        <h2 id="dh-rr-title" className={adminStyles.ctitle}>檢索回歸</h2>
        {q.data && <span className={`${styles.pill} ${STATUS_CLASS[q.data.status]}`}>{STATUS_LABELS[q.data.status]}</span>}
      </div>
      {q.isPending && <p className={adminStyles.idle}>載入中…</p>}
      {q.isError && (
        <p className={adminStyles.error}>
          檢索回歸載入失敗：{q.error instanceof Error && q.error.message ? q.error.message : '請重試'}
        </p>
      )}
      {q.data && <Body d={q.data} />}
    </section>
  )
}

function Body({ d }: { d: RetrievalRegressionResponse }) {
  if (!d.available) {
    return (
      <p className={adminStyles.idle}>
        {d.unavailable_reason === 'missing' ? '還沒有結果檔' : `結果檔無法讀取（${d.unavailable_reason ?? ''}）`}。
        {SCHEDULE}；第一次啟用前要先在主機上擷取基準（<code>scripts/retrieval_regression.py capture</code>）。
      </p>
    )
  }
  const reason = d.reason ? REASON_LABELS[d.reason] : null
  return (
    <>
      <p className={styles.meta}>
        <span>最後一次執行 <b>{fmtDateTime(d.finished_at)}</b></span>
        {d.exit_code != null && <span>退出碼 <b>{d.exit_code}</b></span>}
        <span>{SCHEDULE}</span>
      </p>
      {d.outcome === 'skipped' && (
        <p className={adminStyles.hint}>最近一次略過：{reason ?? '原因不明'}（不告警）。下面是上一次真的比對的結果。</p>
      )}
      {d.outcome === 'error' && (
        <p className={adminStyles.error}>無法比對（{reason ?? '原因不明'}）：{d.message}</p>
      )}
      {d.outcome === 'degraded' && d.message && <p className={adminStyles.error}>{d.message}</p>}
      {d.stale && (
        <p className={styles.warnNote}>超過 48 小時沒有真的比對過：確認 timer 有在跑、或連續被略過的原因。</p>
      )}
      {d.comparison ? <Comparison c={d.comparison} /> : (
        <p className={adminStyles.hint}>還沒有任何一次比對結果。</p>
      )}
    </>
  )
}
