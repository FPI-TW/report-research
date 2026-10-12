import type { ReactNode } from 'react'
import { Card } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type { AnalyticsCell, AnalyticsOverviewResponse, AnalyticsTopList } from '../../../lib/generated/adminApi'
import { BarChart, LineChart } from './AnalyticsCharts'
import {
  AUDIT_ACTION_LABELS, ROUTE_TITLES, cellText, cellTitle, fmtInt, fmtMs, fmtScore, hiddenNote, routeValueLabel,
  spanText,
} from './analyticsFormat'
import {
  type AnalyticsQuery, useAnalyticsOperations, useAnalyticsOverview, useAnalyticsQuality, useAnalyticsRoutes,
  useAnalyticsTop,
} from './useAnalytics'
import styles from './Analytics.module.css'

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '載入失敗，請重試'
}

function Loading<T>({ q, what, children }: {
  q: { data: T | undefined; isPending: boolean; isError: boolean; error: unknown }
  what: string
  children: (data: T) => ReactNode
}) {
  if (q.isPending) return <p className={c.empty}>載入中…</p>
  if (q.isError || q.data === undefined) {
    return <div className={c.cardBody}><p className={c.error} role="alert">{what}載入失敗：{messageOf(q.error)}</p></div>
  }
  return <>{children(q.data)}</>
}

function Stat({ name, value, hint }: { name: string; value: string; hint?: string }) {
  return (
    <div className={c.tile} role="group" aria-label={name}>
      <span className={c.tileLabel}>{name}</span>
      <span className={`${c.tileValue} ${styles.statValue}`}>{value}</span>
      {hint && <span className={c.tileSub} title={hint}>{hint}</span>}
    </div>
  )
}

/** 總覽：數字磚＋每日趨勢。總量不設門檻。 */
export function OverviewSection({ query }: { query: AnalyticsQuery }) {
  const q = useAnalyticsOverview(query)
  return (
    <section className={styles.overview} aria-label="總覽">
      {q.isPending ? (
        <div className={c.card}><p className={c.empty}>載入中…</p></div>
      ) : q.isError || q.data === undefined ? (
        <div className={c.card}>
          <div className={c.cardBody}><p className={c.error} role="alert">總覽載入失敗：{messageOf(q.error)}</p></div>
        </div>
      ) : <Overview d={q.data} />}
    </section>
  )
}

function Overview({ d }: { d: AnalyticsOverviewResponse }) {
  const days = d.daily.map(p => p.day)
  const today = d.range.today
  return (
    <>
      <div className={styles.stats}>
        <Stat name="問答數" value={fmtInt(d.totals.questions)} hint={`其中停止 ${fmtInt(d.totals.stopped)}`} />
        <Stat name="單日活躍人數（最高）" value={fmtInt(d.totals.active_users_peak)}
          hint="問答或閱讀、原檔、搜尋的不重複人數" />
        <Stat name="期間不重複使用者" value={fmtInt(d.totals.distinct_users_live)}
          hint={d.totals.distinct_users_live == null ? '只在即時段可算' : '即時段'} />
        <Stat name="閱讀／原檔／搜尋" value={`${fmtInt(d.totals.reading)}／${fmtInt(d.totals.report_file)}／${fmtInt(d.totals.search)}`}
          hint="成功開啟的次數" />
        <Stat name="回答延遲 p50／p95" value={`${fmtMs(d.latency.p50_ms)}／${fmtMs(d.latency.p95_ms)}`}
          hint={`即時段 ${fmtInt(d.latency.n)} 題，不含停止`} />
        <Stat name="首字時間 p50／p95" value={`${fmtMs(d.latency.thinking_p50_ms)}／${fmtMs(d.latency.thinking_p95_ms)}`} />
      </div>
      <Card title="每日趨勢" labelledBy="analytics-daily-title"
        right={(
          <ul className={styles.sources} aria-label="資料來源">
            {d.range.spans.map(s => <li key={s.source}>{spanText(s)}</li>)}
          </ul>
        )}>
        <div className={c.cardBody}>
          {d.totals.missing_days > 0 && (
            <p className={`${c.banner} ${styles.missing}`} data-tone="warn">
              有 {d.totals.missing_days} 天還沒有每晚彙總（圖上灰色）：確認 report-mark-analytics-rollup.timer 有在跑，
              或以 scripts/analytics_rollup.py --backfill 補上。
            </p>
          )}
          <div className={styles.charts}>
            <BarChart title="每日問答數" days={days} today={today} values={d.daily.map(p => p.questions ?? null)} />
            <BarChart title="每日活躍人數" days={days} today={today} values={d.daily.map(p => p.active_users ?? null)} />
            <BarChart title="每日閱讀次數" days={days} today={today} values={d.daily.map(p => p.reading)} />
            <LineChart title="每日回答延遲" days={days} format={fmtMs}
              a={{ label: 'p50', values: d.daily.map(p => p.latency_p50_ms ?? null) }}
              b={{ label: 'p95', values: d.daily.map(p => p.latency_p95_ms ?? null) }} />
          </div>
        </div>
      </Card>
    </>
  )
}

function RankList({ title, list, minUsers, showKey = false, emptyText = '這段期間沒有資料' }: {
  title: string
  list: AnalyticsTopList
  minUsers: number
  showKey?: boolean
  emptyText?: string
}) {
  const max = Math.max(1, ...list.cells.map(c => c.value ?? 0))
  const note = hiddenNote(list, minUsers)
  return (
    <div>
      <h3 className={styles.listTitle}>{title}</h3>
      {list.cells.length === 0 ? (
        <p className={styles.listEmpty}>{note ?? emptyText}</p>
      ) : (
        <ol className={styles.rank} aria-label={title}>
          {list.cells.map(c => <RankItem key={c.key} cell={c} max={max} minUsers={minUsers} showKey={showKey} />)}
        </ol>
      )}
      {list.cells.length > 0 && note && <p className={styles.suppressedNote}>{note}</p>}
    </div>
  )
}

function RankItem({ cell, max, minUsers, showKey }: { cell: AnalyticsCell; max: number; minUsers: number; showKey: boolean }) {
  const label = cell.label || cell.key
  return (
    <li className={styles.rankItem}>
      <span className={styles.rankLabel} title={cell.label ? `${cell.label}（${cell.key}）` : cell.key}>
        {label}{showKey && cell.label && <span className={styles.rankKey}>{cell.key}</span>}
      </span>
      <span className={cell.suppressed ? `${styles.rankValue} ${styles.suppressed}` : styles.rankValue}
        title={cellTitle(cell, minUsers)}>
        {cellText(cell, minUsers)}
      </span>
      <span className={styles.rankTrack} aria-hidden="true">
        <span className={styles.rankFill} style={{ width: cell.suppressed ? 0 : `${((cell.value ?? 0) / max) * 100}%` }} />
      </span>
    </li>
  )
}

/** 熱門：問答引用與閱讀／原檔／搜尋。全部受 k 門檻約束。 */
export function TopSection({ query }: { query: AnalyticsQuery }) {
  const q = useAnalyticsTop(query)
  return (
    <Card title="熱門標的與研報" labelledBy="analytics-top-title">
      <Loading q={q} what="熱門清單">
        {d => {
          const k = d.range.min_users
          return (
            <div className={`${c.cardBody} ${styles.lists}`}>
              <RankList title="問答引用的標的" list={d.targets} minUsers={k} showKey />
              <RankList title="問答引用的研報" list={d.reports} minUsers={k} />
              <RankList title="問答引用的市場" list={d.markets} minUsers={k} showKey />
              <RankList title="閱讀頁開啟" list={d.reading} minUsers={k} />
              <RankList title="原檔開啟" list={d.report_file} minUsers={k} />
              <RankList title="搜尋的市場篩選" list={d.search_markets} minUsers={k} showKey
                emptyText="這段期間沒有帶市場篩選的搜尋" />
            </div>
          )
        }}
      </Loading>
    </Card>
  )
}

/** 路由分布與 LLM 異常計數。只有路由類別受 k 門檻約束。 */
export function RoutesSection({ query }: { query: AnalyticsQuery }) {
  const q = useAnalyticsRoutes(query)
  return (
    <Card title="路由分布" labelledBy="analytics-routes-title">
      <Loading q={q} what="路由分布">
        {d => {
          const k = d.range.min_users
          return (
            <div className={c.cardBody}>
              <p className={styles.counters}>
                <span>問答 <b>{fmtInt(d.questions)}</b></span>
                <span>停止 <b>{fmtInt(d.stopped)}</b></span>
                <span>LLM 截斷 <b>{fmtInt(d.llm_truncated)}</b></span>
                <span>含無效引用的回答 <b>{fmtInt(d.invalid_citation_rows)}</b>（共 {fmtInt(d.invalid_citations)} 處）</span>
              </p>
              <div className={styles.lists}>
                {d.distributions.map(dist => (
                  <RankList key={dist.name} title={ROUTE_TITLES[dist.name] ?? dist.name} minUsers={k}
                    list={{
                      cells: dist.cells.map(cell => ({ ...cell, label: routeValueLabel(dist.name, cell.key) })),
                      suppressed_count: dist.suppressed_count, complementary_count: dist.complementary_count,
                      truncated: false,
                    }} />
                ))}
              </div>
            </div>
          )
        }}
      </Loading>
    </Card>
  )
}

/** 忠實度（只計現行 judge）與回饋的週趨勢。 */
export function QualitySection({ query }: { query: AnalyticsQuery }) {
  const q = useAnalyticsQuality(query)
  return (
    <Card title="品質趨勢（每週）" labelledBy="analytics-quality-title">
      <Loading q={q} what="品質趨勢">
        {d => {
          return (
            <>
              <p className={`${styles.counters} ${styles.countersBar}`}>
                <span>現行 judge <b>{d.judge_model}</b></span>
                <span>低分門檻 <b>{d.faithfulness_min}</b></span>
                {d.other_judge_checked > 0 && <span>其他 judge 量的 {fmtInt(d.other_judge_checked)} 筆不計入分數</span>}
              </p>
              <div className={c.tableWrap}>
                <table className={c.table} aria-label="每週品質">
                  <thead>
                    <tr>
                      <th>週（週一起）</th><th>問答</th><th>已查核</th><th>平均忠實度</th><th>低於門檻</th>
                      <th>降級</th><th>讚</th><th>倒讚</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.weeks.map(w => (
                      <tr key={w.week_start}>
                        <td className={c.num}>{w.week_start}{w.partial && <span className={c.muted}>（不完整）</span>}</td>
                        <td className={c.num}>{fmtInt(w.questions)}</td>
                        <td className={c.num}>{fmtInt(w.judge_checked)}</td>
                        <td className={c.num}>{fmtScore(w.avg_score)}{w.score_n > 0 && <span className={c.muted}>（{w.score_n}）</span>}</td>
                        <td className={c.num}>{w.below_min ? <b>{fmtInt(w.below_min)}</b> : 0}</td>
                        <td className={c.num}>{fmtInt(w.degraded)}</td>
                        <td className={c.num}>{fmtInt(w.likes)}</td>
                        <td className={c.num}>{w.dislikes ? <b>{fmtInt(w.dislikes)}</b> : 0}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )
        }}
      </Loading>
    </Card>
  )
}

/** 上傳與審核量（每週）。 */
export function OperationsSection({ query }: { query: AnalyticsQuery }) {
  const q = useAnalyticsOperations(query)
  return (
    <Card title="上傳與審核量" labelledBy="analytics-ops-title">
      <Loading q={q} what="上傳與審核量">
        {d => {
          return (
            <>
              <p className={`${styles.counters} ${styles.countersBar}`}>
                {d.audit_actions.map(a => (
                  <span key={a.action}>{AUDIT_ACTION_LABELS[a.action] ?? a.action} <b>{fmtInt(a.count)}</b></span>
                ))}
              </p>
              <div className={c.tableWrap}>
                <table className={c.table} aria-label="每週上傳與審核">
                  <thead>
                    <tr>
                      <th>週（週一起）</th><th>收檔</th><th>發布</th><th>退回</th><th>感染</th><th>失敗</th>
                      <th>待複核處理</th><th>原文調閱</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.weeks.map(w => (
                      <tr key={w.week_start}>
                        <td className={c.num}>{w.week_start}{w.partial && <span className={c.muted}>（不完整）</span>}</td>
                        <td className={c.num}>{fmtInt(w.uploads_received)}</td>
                        <td className={c.num}>{fmtInt(w.uploads_published)}</td>
                        <td className={c.num}>{fmtInt(w.uploads_rejected)}</td>
                        <td className={c.num}>{w.uploads_infected ? <b>{fmtInt(w.uploads_infected)}</b> : 0}</td>
                        <td className={c.num}>{fmtInt(w.uploads_failed)}</td>
                        <td className={c.num}>{fmtInt(w.reviews)}</td>
                        <td className={c.num}>{fmtInt(w.qa_content_reads)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )
        }}
      </Loading>
    </Card>
  )
}
