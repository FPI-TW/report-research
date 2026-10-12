import { Link } from 'react-router'
import { Card, TrendBars } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type { AnalyticsOverviewResponse } from '../../../lib/generated/adminApi'
import { fmtInt, shortDay } from '../analytics/analyticsFormat'
import { USAGE_DAYS, useOverviewUsage } from './overviewData'
import styles from '../Overview.module.css'

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '載入失敗，請重試'
}

/** 毫秒 → 數字與單位分開（數字用大字、單位用小字）。 */
function latency(ms: number | null | undefined): { v: string; u?: string } {
  if (ms == null) return { v: '—' }
  return ms < 1000 ? { v: String(Math.round(ms)), u: 'ms' } : { v: (ms / 1000).toFixed(1), u: '秒' }
}

function Usage({ d }: { d: AnalyticsOverviewResponse }) {
  // 今天那一格：範圍的最後一天就是後端的「今天」（台北時間）；對不上（跨日瞬間）就不硬湊。
  const today = d.daily.find(p => p.day === d.range.today)
  const p95 = latency(d.latency.p95_ms)
  const stats: { l: string; v: string; u?: string; s: string }[] = [
    { l: '問答', v: fmtInt(today?.questions), s: '今日・台北時間' },
    { l: '活躍使用者', v: fmtInt(today?.active_users), s: '今日' },
    { l: '延遲 p95', ...p95, s: `近 ${USAGE_DAYS} 天` },
    { l: '閱讀', v: fmtInt(today?.reading), s: '今日・成功開啟' },
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
          <span>每日問答</span>
          {first && last && <span className={c.mono}>{shortDay(first)}–{shortDay(last)}</span>}
        </div>
        <TrendBars label="每日問答" bars={d.daily.map(p => ({ value: p.questions ?? null, label: shortDay(p.day) }))}
          format={v => `${fmtInt(v)} 則`} highlightLast={last === d.range.today} />
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
    <Card title="今日用量" labelledBy="overview-usage" right={<Link to={analyticsTo} className={c.link}>使用分析</Link>}>
      {q.isPending ? (
        <p className={c.empty}>載入中…</p>
      ) : q.isError ? (
        <div className={c.cardBody}><p className={c.error} role="alert">今日用量載入失敗：{messageOf(q.error)}</p></div>
      ) : <Usage d={q.data} />}
    </Card>
  )
}
