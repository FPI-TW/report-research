import { useState } from 'react'
import { Chip } from '../../../../components/console'
import c from '../../../../components/console/Console.module.css'
import type { IncidentTrendsResponse } from '../../../../lib/generated/adminApi'
import { fmtDateTime } from '../../auditLabels'
import { OpsQueryError } from '../OpsShared'
import { componentLabel, fmtDuration } from '../opsLabels'
import { useIncidentTrends } from '../useDbInsights'
import styles from './Incidents.module.css'

const TREND_RANGES = [
  { days: 30, label: '30 天' },
  { days: 90, label: '90 天' },
  { days: 180, label: '180 天' },
  { days: 365, label: '1 年' },
]

function fmtRate(v: number | null | undefined): string {
  return v == null ? '—' : `${(v * 100).toFixed(1)}%`
}

function TrendStat({ name, value, hint }: { name: string; value: string; hint?: string }) {
  return (
    <div className={`${c.tile} ${styles.tile}`} role="group" aria-label={name}>
      <span className={c.tileLabel}>{name}</span>
      <span className={`${c.tileValue} ${styles.tileValue}`}>{value}</span>
      {hint && <span className={c.tileSub}>{hint}</span>}
    </div>
  )
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
        aria-label={`每週事件數：${rows.map(([w, v]) => `${w} 嚴重 ${v.critical}、警告 ${v.warning}`).join('；')}`}>
        {rows.map(([week, v]) => (
          <div key={week} className={styles.week} title={`${week} 起的一週：嚴重 ${v.critical}、警告 ${v.warning}`}>
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
          <li>最高一週 {max} 件</li>
        </ul>
      </figcaption>
    </figure>
  )
}

function Trends({ data }: { data: IncidentTrendsResponse }) {
  const s = data.summary
  return (
    <>
      <div className={c.tiles}>
        <TrendStat name="事件數" value={String(s.total)} hint={`嚴重 ${s.critical}・警告 ${s.warning}`} />
        <TrendStat name="平均恢復時間（MTTR）" value={fmtDuration(s.mttr_seconds)} hint={`只算已恢復的 ${s.resolved} 件`} />
        <TrendStat name="持續時間 p50／p90" value={`${fmtDuration(s.p50_seconds)}／${fmtDuration(s.p90_seconds)}`} />
        <TrendStat name="結束不明／進行中" value={`${s.lost}／${s.firing}`} hint="結束不明不納入 MTTR" />
      </div>
      {data.weeks.length === 0 ? (
        <p className={styles.note}>區間內沒有開過事件。</p>
      ) : (
        <>
          <div>
            <h3 className={styles.subTitle}>每週事件數</h3>
            <WeeklyBars weeks={data.weeks} />
          </div>
          <div>
            <h3 className={styles.subTitle}>各元件</h3>
            <div className={`${c.tableWrap} ${styles.subTable}`}>
              <table className={c.table} aria-label="各元件事件統計">
                <thead>
                  <tr><th>元件</th><th>件數</th><th>已恢復</th><th>結束不明</th><th>進行中</th><th>MTTR</th><th>p50</th><th>p90</th></tr>
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
            <h3 className={styles.subTitle}>最常見的原因</h3>
            <div className={`${c.tableWrap} ${styles.subTable}`}>
              <table className={c.table} aria-label="最常見的原因">
                <thead><tr><th>原因</th><th>件數</th><th>元件</th></tr></thead>
                <tbody>
                  {data.top_reasons.map(r => (
                    <tr key={r.reason}>
                      <td className={c.mono}>{r.reason}</td>
                      <td className={c.num}>{r.total}</td>
                      <td>{r.components.map(componentLabel).join('、')}</td>
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
          排程工作失敗率
          <span className={styles.subHint}>{fmtDateTime(data.jobs_since)} ～ {fmtDateTime(data.jobs_until)}（90 天）</span>
        </h3>
        {data.jobs.length === 0 ? (
          <p className={styles.note}>沒有排程工作的執行紀錄（收集器與匯入器沒裝時是空的）。</p>
        ) : (
          <div className={`${c.tableWrap} ${styles.subTable}`}>
            <table className={c.table} aria-label="排程工作失敗率">
              <thead><tr><th>unit</th><th>執行</th><th>失敗</th><th>失敗率</th><th>結果不明</th><th>最近失敗</th></tr></thead>
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
        失敗＝執行完但 systemd 的結果不是 success（unit 設定放行的退出碼本來就算 success）；失敗率的分母是執行完的次數，
        「結果不明」（沒觀測到結束）另列。
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
        <h2 id="ops-incident-trends-title" className={c.cardTitle}>事件趨勢</h2>
        <div className={c.cardRight}>
          <label className={styles.inlineField}>
            區間
            <select className={c.select} value={days} onChange={e => setDays(Number(e.target.value))}>
              {TREND_RANGES.map(r => <option key={r.days} value={r.days}>{r.label}</option>)}
            </select>
          </label>
        </div>
      </div>
      <div className={styles.trendBody}>
        {q.isPending ? <p className={styles.note}>載入中…</p> : q.isError ? (
          <OpsQueryError error={q.error} what="事件趨勢" />
        ) : (
          <Trends data={q.data} />
        )}
      </div>
    </section>
  )
}
