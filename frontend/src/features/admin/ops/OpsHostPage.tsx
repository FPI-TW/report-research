import type { ObservationItem } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { OpsQueryError } from './OpsShared'
import { useHostObservations } from './useOps'
import adminStyles from '../Admin.module.css'
import styles from './Ops.module.css'

type Latest = Map<string, Map<string, ObservationItem>>

/** 依 subject、metric 取最新一筆（後端回新→舊，第一次出現的就是最新）。 */
function latestBySubject(items: ObservationItem[]): Latest {
  const out: Latest = new Map()
  for (const it of items) {
    let m = out.get(it.subject)
    if (!m) { m = new Map(); out.set(it.subject, m) }
    if (!m.has(it.metric)) m.set(it.metric, it)
  }
  return out
}

function peak(items: ObservationItem[], subject: string, metric: string): number | null {
  let best: number | null = null
  for (const it of items) {
    if (it.subject === subject && it.metric === metric && it.value != null && (best == null || it.value > best)) {
      best = it.value
    }
  }
  return best
}

function fmtPct(v: number | null | undefined): string {
  return v == null ? '—' : `${v.toFixed(1)}%`
}

function fmtBytes(v: number | null | undefined): string {
  if (v == null) return '—'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let n = v
  let i = 0
  while (Math.abs(n) >= 1024 && i < units.length - 1) { n /= 1024; i += 1 }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

function fmtNum(v: number | null | undefined, digits = 2): string {
  return v == null ? '—' : v.toFixed(digits)
}

function Stat({ name, value, hint }: { name: string; value: string; hint?: string }) {
  return (
    <div className={styles.tierCard} role="group" aria-label={name}>
      <div className={styles.tierName}>{name}</div>
      <div className={styles.tierCount}>{value}</div>
      {hint && <div className={styles.tierHint}>{hint}</div>}
    </div>
  )
}

/**
 * 主機：最近一小時的主機觀測（CPU、記憶體、磁碟、I/O PSI、load）取最新值，CPU／記憶體／I/O 壓力另附一小時
 * 最高值；下方是檔案系統用量。資料來自 DB 投影（收集器每 60 秒寫 spool、loader 每 5 分鐘匯入），不經維運代理。
 */
export default function OpsHostPage() {
  const q = useHostObservations()
  if (q.isPending) return <p className={adminStyles.idle}>載入中…</p>
  if (q.isError) return <OpsQueryError error={q.error} what="主機觀測" />

  const items = q.data.items
  const latest = latestBySubject(items)
  const host = latest.get('host')
  const v = (metric: string) => host?.get(metric)?.value ?? null
  const newest = items[0]
  const filesystems = [...latest.entries()].filter(([subject]) => subject.startsWith('fs:'))

  return (
    <section className={adminStyles.card} aria-labelledby="ops-host-title">
      <h2 id="ops-host-title" className={adminStyles.ctitle}>主機</h2>
      {items.length === 0 ? (
        <p className={adminStyles.idle}>
          最近一小時沒有主機觀測。確認主機上的收集器（report-mark-metrics）與匯入器
          （report-mark-load-observations.timer）都在跑；資料每 5 分鐘匯入一次。
        </p>
      ) : (
        <>
          <p className={styles.meta}>
            <span>{newest.host}</span>
            <span>最新觀測 <b>{fmtDateTime(newest.observed_at)}</b>（每 5 分鐘匯入，最多晚一輪）</span>
            {q.data.truncated && <span>資料超過上限，只顯示最近的部分</span>}
          </p>
          <div className={adminStyles.spacer} />
          <div className={styles.tierGrid}>
            <Stat name="CPU 使用率" value={fmtPct(v('cpu_pct'))} hint={`1 小時最高 ${fmtPct(peak(items, 'host', 'cpu_pct'))}・等待 I/O ${fmtPct(v('iowait_pct'))}`} />
            <Stat name="記憶體使用率" value={fmtPct(v('mem_used_pct'))} hint={`可用 ${fmtBytes(v('mem_avail_bytes'))}・Swap ${fmtBytes(v('swap_used_bytes'))}`} />
            <Stat name="Load" value={fmtNum(v('load1'))} hint={`5 分 ${fmtNum(v('load5'))}・15 分 ${fmtNum(v('load15'))}`} />
            <Stat
              name="I/O 壓力（PSI some，60 秒）"
              value={fmtPct(v('psi_io_some_avg60'))}
              hint={`full ${fmtPct(v('psi_io_full_avg60'))}・1 小時最高 ${fmtPct(peak(items, 'host', 'psi_io_some_avg60'))}`}
            />
            <Stat name="磁碟讀寫" value={`${fmtBytes(v('disk_read_bps'))}/s`} hint={`寫 ${fmtBytes(v('disk_write_bps'))}/s・${fmtNum(v('disk_iops'), 0)} IOPS`} />
            <Stat
              name="CPU／記憶體壓力（PSI，60 秒）"
              value={fmtPct(v('psi_cpu_some_avg60'))}
              hint={`記憶體 some ${fmtPct(v('psi_memory_some_avg60'))}・full ${fmtPct(v('psi_memory_full_avg60'))}`}
            />
          </div>
          {filesystems.length > 0 && (
            <>
              <div className={adminStyles.spacer} />
              <div className={adminStyles.tableWrap}>
                <table className={adminStyles.table} aria-label="檔案系統">
                  <thead><tr><th>檔案系統</th><th>使用率</th><th>可用</th><th>總量</th></tr></thead>
                  <tbody>
                    {filesystems.map(([subject, m]) => (
                      <tr key={subject}>
                        <td className={adminStyles.wrapCell}>{subject.slice(3)}</td>
                        <td className={adminStyles.num}>{fmtPct(m.get('used_pct')?.value)}</td>
                        <td className={adminStyles.num}>{fmtBytes(m.get('avail_bytes')?.value)}</td>
                        <td className={adminStyles.num}>{fmtBytes(m.get('size_bytes')?.value)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </section>
  )
}
