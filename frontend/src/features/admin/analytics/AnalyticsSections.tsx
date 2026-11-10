import type { ReactNode } from 'react'
import { Card, StatTile } from '../../../components/console'
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
import { t } from '@/i18n'

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : t('admin.analytics.analyticsSections.couldnTLoadTryAgain')
}

function Loading<T>({ q, what, children }: {
  q: { data: T | undefined; isPending: boolean; isError: boolean; error: unknown }
  what: string
  children: (data: T) => ReactNode
}) {
  if (q.isPending) return <p className={c.empty}>{t('admin.analytics.analyticsSections.loading')}</p>
  if (q.isError || q.data === undefined) {
    return <div className={c.cardBody}><p className={c.error} role="alert">{t('admin.analytics.analyticsSections.couldnTLoad', { what, error: messageOf(q.error) })}</p></div>
  }
  return <>{children(q.data)}</>
}

/** 總覽：數字磚＋每日趨勢。總量不設門檻。 */
export function OverviewSection({ query }: { query: AnalyticsQuery }) {
  const q = useAnalyticsOverview(query)
  return (
    <section className={styles.overview} aria-label={t('admin.analytics.analyticsSections.overview')}>
      {q.isPending ? (
        <div className={c.card}><p className={c.empty}>{t('admin.analytics.analyticsSections.loading')}</p></div>
      ) : q.isError || q.data === undefined ? (
        <div className={c.card}>
          <div className={c.cardBody}><p className={c.error} role="alert">{t('admin.analytics.analyticsSections.couldnTLoadOverview', { error: messageOf(q.error) })}</p></div>
        </div>
      ) : <Overview d={q.data} />}
    </section>
  )
}

function Overview({ d }: { d: AnalyticsOverviewResponse }) {
  const days = d.daily.map(p => p.day)
  const today = d.range.today
  const stats: { name: string; value: string; hint?: string }[] = [
    { name: t('admin.analytics.analyticsSections.qACount'), value: fmtInt(d.totals.questions), hint: t('admin.analytics.analyticsSections.stopped', { stopped: fmtInt(d.totals.stopped) }) },
    { name: t('admin.analytics.analyticsSections.dailyActiveUsersPeak'), value: fmtInt(d.totals.active_users_peak), hint: t('admin.analytics.analyticsSections.distinctUsersOfQA') },
    { name: t('admin.analytics.analyticsSections.distinctUsersInPeriod'), value: fmtInt(d.totals.distinct_users_live),
      hint: d.totals.distinct_users_live == null ? t('admin.analytics.analyticsSections.onlyAvailableForTheLive') : t('admin.analytics.analyticsSections.liveRange') },
    { name: t('admin.analytics.analyticsSections.readingOriginalSearch'), value: t('admin.analytics.analyticsSections.text', { reading: fmtInt(d.totals.reading), report_file: fmtInt(d.totals.report_file), search: fmtInt(d.totals.search) }),
      hint: t('admin.analytics.analyticsSections.successfulOpens') },
    { name: t('admin.analytics.analyticsSections.answerLatencyP50P95'), value: t('admin.analytics.analyticsSections.text2', { p50_ms: fmtMs(d.latency.p50_ms), p95_ms: fmtMs(d.latency.p95_ms) }),
      hint: t('admin.analytics.analyticsSections.questionsInLiveRangeExcluding', { n: fmtInt(d.latency.n) }) },
    { name: t('admin.analytics.analyticsSections.timeToFirstTokenP50'), value: t('admin.analytics.analyticsSections.text3', { thinking_p50_ms: fmtMs(d.latency.thinking_p50_ms), thinking_p95_ms: fmtMs(d.latency.thinking_p95_ms) }) },
  ]
  return (
    <>
      <div className={styles.stats}>
        {stats.map(s => (
          <StatTile key={s.name} name={s.name} label={s.name} value={<span className={styles.statValue}>{s.value}</span>}
            sub={s.hint} subTitle={s.hint} />
        ))}
      </div>
      <Card title={t('admin.analytics.analyticsSections.dailyTrend')} labelledBy="analytics-daily-title"
        right={(
          <ul className={styles.sources} aria-label={t('admin.analytics.analyticsSections.dataSource')}>
            {d.range.spans.map(s => <li key={s.source}>{spanText(s)}</li>)}
          </ul>
        )}>
        <div className={c.cardBody}>
          {d.totals.missing_days > 0 && (
            <p className={`${c.banner} ${styles.missing}`} data-tone="warn">
              {t('admin.analytics.analyticsSections.daysHaveNoNightlyRollup', { missing_days: d.totals.missing_days })}
            </p>
          )}
          <div className={styles.charts}>
            <BarChart title={t('admin.analytics.analyticsSections.dailyQACount')} days={days} today={today} values={d.daily.map(p => p.questions ?? null)} />
            <BarChart title={t('admin.analytics.analyticsSections.dailyActiveUsers')} days={days} today={today} values={d.daily.map(p => p.active_users ?? null)} />
            <BarChart title={t('admin.analytics.analyticsSections.dailyReads')} days={days} today={today} values={d.daily.map(p => p.reading)} />
            <LineChart title={t('admin.analytics.analyticsSections.dailyAnswerLatency')} days={days} format={fmtMs}
              a={{ label: 'p50', values: d.daily.map(p => p.latency_p50_ms ?? null) }}
              b={{ label: 'p95', values: d.daily.map(p => p.latency_p95_ms ?? null) }} />
          </div>
        </div>
      </Card>
    </>
  )
}

function RankList({ title, list, minUsers, showKey = false, emptyText = t('admin.analytics.analyticsSections.noDataInThisPeriod') }: {
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
      <span className={styles.rankLabel} title={cell.label ? t('admin.analytics.analyticsSections.text4', { label: cell.label, key: cell.key }) : cell.key}>
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
    <Card title={t('admin.analytics.analyticsSections.topInstrumentsAndReports')} labelledBy="analytics-top-title">
      <Loading q={q} what={t('admin.analytics.analyticsSections.topLists')}>
        {d => {
          const k = d.range.min_users
          return (
            <div className={`${c.cardBody} ${styles.lists}`}>
              <RankList title={t('admin.analytics.analyticsSections.instrumentsCitedInQA')} list={d.targets} minUsers={k} showKey />
              <RankList title={t('admin.analytics.analyticsSections.reportsCitedInQA')} list={d.reports} minUsers={k} />
              <RankList title={t('admin.analytics.analyticsSections.marketsCitedInQA')} list={d.markets} minUsers={k} showKey />
              <RankList title={t('admin.analytics.analyticsSections.readingPageOpens')} list={d.reading} minUsers={k} />
              <RankList title={t('admin.analytics.analyticsSections.originalFileOpens')} list={d.report_file} minUsers={k} />
              <RankList title={t('admin.analytics.analyticsSections.marketFiltersInSearch')} list={d.search_markets} minUsers={k} showKey
                emptyText={t('admin.analytics.analyticsSections.noSearchesWithAMarket')} />
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
    <Card title={t('admin.analytics.analyticsSections.routeDistribution')} labelledBy="analytics-routes-title">
      <Loading q={q} what={t('admin.analytics.analyticsSections.routeDistribution')}>
        {d => {
          const k = d.range.min_users
          return (
            <div className={c.cardBody}>
              <p className={styles.counters}>
                <span>{t('admin.analytics.analyticsSections.qA')}{' '}<b>{fmtInt(d.questions)}</b></span>
                <span>{t('admin.analytics.analyticsSections.stopped2')}{' '}<b>{fmtInt(d.stopped)}</b></span>
                <span>{t('admin.analytics.analyticsSections.llmTruncated')}{' '}<b>{fmtInt(d.llm_truncated)}</b></span>
                <span>{t('admin.analytics.analyticsSections.answersWithInvalidCitations')}{' '}<b>{fmtInt(d.invalid_citation_rows)}</b>{t('admin.analytics.analyticsSections.total', { invalid_citations: fmtInt(d.invalid_citations) })}</span>
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
    <Card title={t('admin.analytics.analyticsSections.qualityTrendWeekly')} labelledBy="analytics-quality-title">
      <Loading q={q} what={t('admin.analytics.analyticsSections.qualityTrend')}>
        {d => {
          return (
            <>
              <p className={`${styles.counters} ${styles.countersBar}`}>
                <span>{t('admin.analytics.analyticsSections.currentJudge')}{' '}<b>{d.judge_model}</b></span>
                <span>{t('admin.analytics.analyticsSections.lowScoreThreshold')}{' '}<b>{d.faithfulness_min}</b></span>
                {d.other_judge_checked > 0 && <span>{t('admin.analytics.analyticsSections.checkedByOtherJudgesAre', { other_judge_checked: fmtInt(d.other_judge_checked) })}</span>}
              </p>
              <div className={c.tableWrap}>
                <table className={c.table} aria-label={t('admin.analytics.analyticsSections.weeklyQuality')}>
                  <thead>
                    <tr>
                      <th>{t('admin.analytics.analyticsSections.weekFromMonday')}</th><th>{t('admin.analytics.analyticsSections.qA')}</th><th>{t('admin.analytics.analyticsSections.checked')}</th><th>{t('admin.analytics.analyticsSections.avgFaithfulness')}</th><th>{t('admin.analytics.analyticsSections.belowThreshold')}</th>
                      <th>{t('admin.analytics.analyticsSections.degraded')}</th><th>{t('admin.analytics.analyticsSections.thumbsUp')}</th><th>{t('admin.analytics.analyticsSections.thumbsDown')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.weeks.map(w => (
                      <tr key={w.week_start}>
                        <td className={c.num}>{w.week_start}{w.partial && <span className={c.muted}>{t('admin.analytics.analyticsSections.incomplete')}</span>}</td>
                        <td className={c.num}>{fmtInt(w.questions)}</td>
                        <td className={c.num}>{fmtInt(w.judge_checked)}</td>
                        <td className={c.num}>{fmtScore(w.avg_score)}{w.score_n > 0 && <span className={c.muted}>{t('admin.analytics.analyticsSections.text5', { score_n: w.score_n })}</span>}</td>
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
    <Card title={t('admin.analytics.analyticsSections.uploadAndReviewVolume')} labelledBy="analytics-ops-title">
      <Loading q={q} what={t('admin.analytics.analyticsSections.uploadAndReviewVolume')}>
        {d => {
          return (
            <>
              <p className={`${styles.counters} ${styles.countersBar}`}>
                {d.audit_actions.map(a => (
                  <span key={a.action}>{AUDIT_ACTION_LABELS[a.action] ?? a.action} <b>{fmtInt(a.count)}</b></span>
                ))}
              </p>
              <div className={c.tableWrap}>
                <table className={c.table} aria-label={t('admin.analytics.analyticsSections.weeklyUploadsAndReviews')}>
                  <thead>
                    <tr>
                      <th>{t('admin.analytics.analyticsSections.weekFromMonday')}</th><th>{t('admin.analytics.analyticsSections.received')}</th><th>{t('admin.analytics.analyticsSections.published')}</th><th>{t('admin.analytics.analyticsSections.rejected')}</th><th>{t('admin.analytics.analyticsSections.infected')}</th><th>{t('admin.analytics.analyticsSections.failed')}</th>
                      <th>{t('admin.analytics.analyticsSections.reviewHandling')}</th><th>{t('admin.analytics.analyticsSections.qAContentAccess')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.weeks.map(w => (
                      <tr key={w.week_start}>
                        <td className={c.num}>{w.week_start}{w.partial && <span className={c.muted}>{t('admin.analytics.analyticsSections.incomplete')}</span>}</td>
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
