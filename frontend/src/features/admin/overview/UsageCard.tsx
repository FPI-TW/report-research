import { Link } from 'react-router'
import { Card, TrendBars } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type { AnalyticsOverviewResponse } from '../../../lib/generated/adminApi'
import { fmtInt, shortDay } from '../analytics/analyticsFormat'
import { USAGE_DAYS, useOverviewUsage } from './overviewData'
import styles from '../Overview.module.css'
import { t } from '@/i18n'

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : t('admin.overview.usageCard.couldnTLoadTryAgain')
}

/** 毫秒 → 數字與單位分開（數字用大字、單位用小字）。 */
function latency(ms: number | null | undefined): { v: string; u?: string } {
  if (ms == null) return { v: '—' }
  return ms < 1000 ? { v: String(Math.round(ms)), u: 'ms' } : { v: (ms / 1000).toFixed(1), u: t('admin.overview.usageCard.s') }
}

function Usage({ d }: { d: AnalyticsOverviewResponse }) {
  // 今天那一格：範圍的最後一天就是後端的「今天」（台北時間）；對不上（跨日瞬間）就不硬湊。
  const today = d.daily.find(p => p.day === d.range.today)
  const p95 = latency(d.latency.p95_ms)
  const stats: { l: string; v: string; u?: string; s: string }[] = [
    { l: t('admin.overview.usageCard.qA'), v: fmtInt(today?.questions), s: t('admin.overview.usageCard.todayTaipeiTime') },
    { l: t('admin.overview.usageCard.activeUsers'), v: fmtInt(today?.active_users), s: t('admin.overview.usageCard.today') },
    { l: t('admin.overview.usageCard.latencyP95'), ...p95, s: t('admin.overview.usageCard.lastDays', { USAGE_DAYS }) },
    { l: t('admin.overview.usageCard.reading'), v: fmtInt(today?.reading), s: t('admin.overview.usageCard.todaySuccessfulOpens') },
  ]
  const first = d.daily[0]?.day
  const last = d.daily[d.daily.length - 1]?.day
  return (
    <>
      <div className={styles.usage}>
        {stats.map(k => (
          <div key={k.l} className={styles.usageCell} role="group" aria-label={k.l}>
            <div className={styles.usageLabel}>{k.l}</div>
            <div className={styles.usageValue}>{k.v}{k.u && <span className={styles.usageUnit}>{k.u}</span>}</div>
            <div className={styles.usageSub}>{k.s}</div>
          </div>
        ))}
      </div>
      <div className={styles.trend}>
        <div className={styles.trendHead}>
          <span>{t('admin.overview.usageCard.dailyQA')}</span>
          {first && last && <span className={c.mono}>{shortDay(first)}–{shortDay(last)}</span>}
        </div>
        <TrendBars label={t('admin.overview.usageCard.dailyQA')} bars={d.daily.map(p => ({ value: p.questions ?? null, label: shortDay(p.day) }))}
          format={v => t('admin.overview.usageCard.text', { v: fmtInt(v) })} highlightLast={last === d.range.today} />
      </div>
    </>
  )
}

/**
 * 「今日用量」：使用分析 API 的總量那一組（問答、活躍、延遲、閱讀），不碰受 k 門檻約束的格子。
 * 分析 API 最細只到「日」，所以趨勢是每日問答、最後一根（今天）金色，不是每小時。
 */
export function UsageCard({ analyticsTo }: { analyticsTo: string }) {
  const q = useOverviewUsage(true)
  return (
    <Card title={t('admin.overview.usageCard.todaySUsage')} labelledBy="overview-usage" right={<Link to={analyticsTo} className={c.link}>{t('admin.overview.usageCard.analytics')}</Link>}>
      {q.isPending ? (
        <p className={c.empty}>{t('admin.overview.usageCard.loading')}</p>
      ) : q.isError ? (
        <div className={c.cardBody}><p className={c.error} role="alert">{t('admin.overview.usageCard.couldnTLoadTodayS', { error: messageOf(q.error) })}</p></div>
      ) : <Usage d={q.data} />}
    </Card>
  )
}
