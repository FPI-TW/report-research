import { useState } from 'react'
import { Chip, StatTile } from '../../../../components/console'
import c from '../../../../components/console/Console.module.css'
import type { IncidentTrendsResponse } from '../../../../lib/generated/adminApi'
import { fmtDateTime } from '../../auditLabels'
import { OpsQueryError } from '../OpsShared'
import { componentLabel, fmtDuration } from '../opsLabels'
import { useIncidentTrends } from '../useDbInsights'
import styles from './Incidents.module.css'
import { t } from '@/i18n'

const TREND_RANGES = [
  { days: 30, get label() { return t('admin.ops.incidents.incidentTrends.30Days') } },
  { days: 90, get label() { return t('admin.ops.incidents.incidentTrends.90Days') } },
  { days: 180, get label() { return t('admin.ops.incidents.incidentTrends.180Days') } },
  { days: 365, get label() { return t('admin.ops.incidents.incidentTrends.1Year') } },
]

function fmtRate(v: number | null | undefined): string {
  return v == null ? '—' : `${(v * 100).toFixed(1)}%`
}

/** 每週（台北時間週一起）依嚴重度堆疊的長條；元件合併計算，元件別的數字看下面的表。 */
function WeeklyBars({ weeks }: { weeks: IncidentTrendsResponse['weeks'] }) {
  const byWeek = new Map<string, { critical: number; warning: number }>()
  for (const w of weeks) {
    const cur = byWeek.get(w.week_start) ?? { critical: 0, warning: 0 }
    if (w.severity === 'CRITICAL') cur.critical += w.total
    else cur.warning += w.total
    byWeek.set(w.week_start, cur)
  }
  const rows = [...byWeek.entries()].sort(([a], [b]) => a.localeCompare(b))
  const max = Math.max(1, ...rows.map(([, v]) => v.critical + v.warning))
  return (
    <figure className={styles.figure}>
      <div className={styles.weeks} role="img"
        aria-label={t('admin.ops.incidents.incidentTrends.incidentsPerWeek', { map: rows.map(([w, v]) => t('admin.ops.incidents.incidentTrends.criticalWarning', { w, critical: v.critical, warning: v.warning })).join(t('admin.ops.incidents.incidentTrends.text')) })}>
        {rows.map(([week, v]) => (
          <div key={week} className={styles.week} title={t('admin.ops.incidents.incidentTrends.weekOfCriticalWarning', { week, critical: v.critical, warning: v.warning })}>
            <div className={styles.barCritical} style={{ height: `${(v.critical / max) * 100}%` }} />
            <div className={styles.barWarning} style={{ height: `${(v.warning / max) * 100}%` }} />
          </div>
        ))}
      </div>
      <figcaption>
        <div className={styles.weekLabels}><span>{rows[0]?.[0]}</span><span>{rows[rows.length - 1]?.[0]}</span></div>
        <ul className={styles.legend}>
          <li><span className={styles.swatchCritical} />CRITICAL</li>
          <li><span className={styles.swatchWarning} />WARNING</li>
          <li>{t('admin.ops.incidents.incidentTrends.peakWeek', { max })}</li>
        </ul>
      </figcaption>
    </figure>
  )
}

function Trends({ data }: { data: IncidentTrendsResponse }) {
  const s = data.summary
  const tiles: { name: string; value: string; hint?: string }[] = [
    { name: t('admin.ops.incidents.incidentTrends.incidents'), value: String(s.total), hint: t('admin.ops.incidents.incidentTrends.criticalWarning2', { critical: s.critical, warning: s.warning }) },
    { name: t('admin.ops.incidents.incidentTrends.meanTimeToRecoveryMttr'), value: fmtDuration(s.mttr_seconds), hint: t('admin.ops.incidents.incidentTrends.onlyTheRecoveredIncidents', { resolved: s.resolved }) },
    { name: t('admin.ops.incidents.incidentTrends.durationP50P90'), value: t('admin.ops.incidents.incidentTrends.text2', { p50_seconds: fmtDuration(s.p50_seconds), p90_seconds: fmtDuration(s.p90_seconds) }) },
    { name: t('admin.ops.incidents.incidentTrends.endUnknownOpen'), value: t('admin.ops.incidents.incidentTrends.text3', { lost: s.lost, firing: s.firing }), hint: t('admin.ops.incidents.incidentTrends.endUnknownIncidentsAreExcluded') },
  ]
  return (
    <>
      <div className={`${c.tiles} ${styles.trendTiles}`}>
        {tiles.map(item => (
          <StatTile key={item.name} name={item.name} label={item.name} value={<span className={styles.tileValue}>{item.value}</span>} sub={item.hint} />
        ))}
      </div>
      {data.weeks.length === 0 ? (
        <p className={styles.note}>{t('admin.ops.incidents.incidentTrends.noIncidentsOpenedInThis')}</p>
      ) : (
        <>
          <div>
            <h3 className={styles.subTitle}>{t('admin.ops.incidents.incidentTrends.incidentsPerWeek2')}</h3>
            <WeeklyBars weeks={data.weeks} />
          </div>
          <div>
            <h3 className={styles.subTitle}>{t('admin.ops.incidents.incidentTrends.byComponent')}</h3>
            <div className={`${c.tableWrap} ${styles.subTable}`}>
              <table className={c.table} aria-label={t('admin.ops.incidents.incidentTrends.incidentStatsByComponent')}>
                <thead>
                  <tr><th>{t('admin.ops.incidents.incidentTrends.component')}</th><th>{t('admin.ops.incidents.incidentTrends.count')}</th><th>{t('admin.ops.incidents.incidentTrends.recovered')}</th><th>{t('admin.ops.incidents.incidentTrends.endUnknown')}</th><th>{t('admin.ops.incidents.incidentTrends.open')}</th><th>MTTR</th><th>p50</th><th>p90</th></tr>
                </thead>
                <tbody>
                  {data.by_component.map(k => (
                    <tr key={k.component}>
                      <td>{componentLabel(k.component)}<div className={c.sub}>{k.component}</div></td>
                      <td className={c.num}>{k.total}</td>
                      <td className={c.num}>{k.resolved}</td>
                      <td className={c.num}>{k.lost}</td>
                      <td className={c.num}>{k.firing}</td>
                      <td className={c.num}>{fmtDuration(k.mttr_seconds)}</td>
                      <td className={c.num}>{fmtDuration(k.p50_seconds)}</td>
                      <td className={c.num}>{fmtDuration(k.p90_seconds)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div>
            <h3 className={styles.subTitle}>{t('admin.ops.incidents.incidentTrends.mostCommonReasons')}</h3>
            <div className={`${c.tableWrap} ${styles.subTable}`}>
              <table className={c.table} aria-label={t('admin.ops.incidents.incidentTrends.mostCommonReasons')}>
                <thead><tr><th>{t('admin.ops.incidents.incidentTrends.reason')}</th><th>{t('admin.ops.incidents.incidentTrends.count')}</th><th>{t('admin.ops.incidents.incidentTrends.component')}</th></tr></thead>
                <tbody>
                  {data.top_reasons.map(r => (
                    <tr key={r.reason}>
                      <td className={c.mono}>{r.reason}</td>
                      <td className={c.num}>{r.total}</td>
                      <td>{r.components.map(componentLabel).join(t('admin.ops.incidents.incidentTrends.text4'))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
      <div>
        <h3 className={styles.subTitle}>
          {t('admin.ops.incidents.incidentTrends.scheduledJobFailureRate')}
          <span className={styles.subHint}>{t('admin.ops.incidents.incidentTrends.90Days2', { jobs_since: fmtDateTime(data.jobs_since), jobs_until: fmtDateTime(data.jobs_until) })}</span>
        </h3>
        {data.jobs.length === 0 ? (
          <p className={styles.note}>{t('admin.ops.incidents.incidentTrends.noScheduledJobRunsEmpty')}</p>
        ) : (
          <div className={`${c.tableWrap} ${styles.subTable}`}>
            <table className={c.table} aria-label={t('admin.ops.incidents.incidentTrends.scheduledJobFailureRate')}>
              <thead><tr><th>unit</th><th>{t('admin.ops.incidents.incidentTrends.runs')}</th><th>{t('admin.ops.incidents.incidentTrends.failed')}</th><th>{t('admin.ops.incidents.incidentTrends.failureRate')}</th><th>{t('admin.ops.incidents.incidentTrends.resultUnknown')}</th><th>{t('admin.ops.incidents.incidentTrends.lastFailure')}</th></tr></thead>
              <tbody>
                {data.jobs.map(j => (
                  <tr key={j.unit}>
                    <td className={c.mono}>{j.unit}</td>
                    <td className={c.num}>{j.runs}</td>
                    <td className={c.num}>{j.failed}</td>
                    <td className={c.num}>
                      {j.failure_rate != null && j.failure_rate > 0
                        ? <Chip tone="bad" mono>{fmtRate(j.failure_rate)}</Chip>
                        : fmtRate(j.failure_rate)}
                    </td>
                    <td className={c.num}>{j.lost}</td>
                    <td className={c.num}>{fmtDateTime(j.last_failure_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <p className={styles.note}>
        {t('admin.ops.incidents.incidentTrends.failedFinishedButSystemdS')}
      </p>
    </>
  )
}

/**
 * 事件趨勢（/api/admin/incidents/trends）：開場時間落在區間內的事件每週件數、MTTR 與持續時間 p50／p90（只算已恢復；
 * 「結束不明」不知道何時結束，只計件數）、最常見的原因，以及各排程工作 90 天內的失敗率。不新表，直接彙總 DB 投影。
 */
export function IncidentTrendsSection() {
  const [days, setDays] = useState(90)
  const q = useIncidentTrends(days)
  return (
    <section className={c.card} aria-labelledby="ops-incident-trends-title">
      <div className={c.cardHead}>
        <h2 id="ops-incident-trends-title" className={c.cardTitle}>{t('admin.ops.incidents.incidentTrends.incidentTrends')}</h2>
        <div className={c.cardRight}>
          <label className={styles.inlineField}>
            {t('admin.ops.incidents.incidentTrends.range')}
            <select className={c.select} value={days} onChange={e => setDays(Number(e.target.value))}>
              {TREND_RANGES.map(r => <option key={r.days} value={r.days}>{r.label}</option>)}
            </select>
          </label>
        </div>
      </div>
      <div className={styles.trendBody}>
        {q.isPending ? <p className={styles.note}>{t('admin.ops.incidents.incidentTrends.loading')}</p> : q.isError ? (
          <OpsQueryError error={q.error} what={t('admin.ops.incidents.incidentTrends.incidentTrends')} />
        ) : (
          <Trends data={q.data} />
        )}
      </div>
    </section>
  )
}
