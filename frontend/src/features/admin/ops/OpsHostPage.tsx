import type { ObservationItem, ObservationListResponse } from '../../../lib/generated/adminApi'
import { Card, Chip, PageHeader, StatTile, TrendBars } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { fmtDateTime } from '../auditLabels'
import { peak, trendAxis, trendSeries } from './exec/hostSeries'
import { OpsQueryError } from './OpsShared'
import { useHostObservations } from './useOps'
import styles from './exec/Exec.module.css'

type Latest = Map<string, Map<string, ObservationItem>>

/** 後端依查詢區間選的粒度（24 小時內逐筆，更早的是 5 分鐘／1 小時聚合，見 revision 0007）。 */
const RESOLUTION_LABEL: Record<ObservationListResponse['resolution'], string> = {
  raw: '原始觀測（每 60 秒）',
  '5m': '5 分鐘聚合',
  '1h': '1 小時聚合',
}

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

/** 百分比類的磚：數字與「%」分開；沒有值（「—」）時不帶單位。 */
function pct(v: number | null): { value: string; unit?: string } {
  return v == null ? { value: '—' } : { value: v.toFixed(1), unit: '%' }
}

/** 位元組速率的磚：「2.0」＋「KiB/s」。 */
function rate(v: number | null): { value: string; unit?: string } {
  if (v == null) return { value: '—' }
  const [n, u] = fmtBytes(v).split(' ')
  return { value: n, unit: `${u}/s` }
}

interface Chart { title: string; metric: string; format: (v: number) => string; max?: number }

/** 趨勢圖：沒有門檻虛線也沒有 warn 色——觀測資料本身不帶門檻，這裡不自己發明數字。 */
const CHARTS: readonly Chart[] = [
  { title: 'CPU 使用率', metric: 'cpu_pct', format: fmtPct, max: 100 },
  { title: '記憶體使用率', metric: 'mem_used_pct', format: fmtPct, max: 100 },
  { title: 'Load（1 分鐘）', metric: 'load1', format: v => fmtNum(v) },
  { title: 'I/O 壓力（PSI some，60 秒）', metric: 'psi_io_some_avg60', format: fmtPct },
]

function fmtPeak(ch: Chart, items: ObservationItem[]): string {
  const p = peak(items, 'host', ch.metric)
  return p == null ? '—' : ch.format(p)
}

/**
 * 主機：最近一小時的主機觀測（CPU、記憶體、磁碟、I/O PSI、load）取最新值，CPU／記憶體／I/O 壓力另附一小時
 * 最高值與趨勢圖；下方是檔案系統用量。資料來自 DB 投影（收集器每 60 秒寫 spool、loader 每 5 分鐘匯入），不經維運代理。
 */
export default function OpsHostPage() {
  const q = useHostObservations()
  const header = (
    <PageHeader
      summary="主機與檔案系統的資源用量，取最近一小時的觀測；每 5 分鐘匯入，最多晚一輪。"
      help={(
        <>
          數字磚是最新一筆觀測，CPU 與 I/O 壓力另附這一小時的最高值；趨勢圖每根柱子是一個觀測區間的最高值，沒有觀測的區間留空。
          資料來自 DB 投影（主機上的收集器每 60 秒寫 spool、匯入器每 5 分鐘匯入一次），不經維運代理，所以代理停掉時這頁照常。
        </>
      )}
    />
  )
  if (q.isPending) return <>{header}<p className={c.empty}>載入中…</p></>
  if (q.isError) return <>{header}<OpsQueryError error={q.error} what="主機觀測" /></>

  const items = q.data.items
  if (items.length === 0) {
    return (
      <>
        {header}
        <p className={c.empty}>
          最近一小時沒有主機觀測。確認主機上的收集器（report-mark-metrics）與匯入器
          （report-mark-load-observations.timer）都在跑；資料每 5 分鐘匯入一次。
        </p>
      </>
    )
  }
  const latest = latestBySubject(items)
  const host = latest.get('host')
  const v = (metric: string) => host?.get(metric)?.value ?? null
  const newest = items[0]
  const filesystems = [...latest.entries()].filter(([subject]) => subject.startsWith('fs:'))
  const axis = trendAxis(q.data)
  const charts = CHARTS.map(ch => ({ ...ch, bars: trendSeries(q.data, 'host', ch.metric) }))
    .filter(ch => ch.bars.some(b => b.value != null))
  const tiles: { name: string; value: string; unit?: string; hint: string }[] = [
    { name: 'CPU 使用率', ...pct(v('cpu_pct')),
      hint: `1 小時最高 ${fmtPct(peak(items, 'host', 'cpu_pct'))}・等待 I/O ${fmtPct(v('iowait_pct'))}` },
    { name: '記憶體使用率', ...pct(v('mem_used_pct')),
      hint: `可用 ${fmtBytes(v('mem_avail_bytes'))}・Swap ${fmtBytes(v('swap_used_bytes'))}` },
    { name: 'Load', value: fmtNum(v('load1')), hint: `5 分 ${fmtNum(v('load5'))}・15 分 ${fmtNum(v('load15'))}` },
    { name: 'I/O 壓力（PSI some，60 秒）', ...pct(v('psi_io_some_avg60')),
      hint: `full ${fmtPct(v('psi_io_full_avg60'))}・1 小時最高 ${fmtPct(peak(items, 'host', 'psi_io_some_avg60'))}` },
    { name: '磁碟讀寫', ...rate(v('disk_read_bps')),
      hint: `寫 ${fmtBytes(v('disk_write_bps'))}/s・${fmtNum(v('disk_iops'), 0)} IOPS` },
    { name: 'CPU／記憶體壓力（PSI，60 秒）', ...pct(v('psi_cpu_some_avg60')),
      hint: `記憶體 some ${fmtPct(v('psi_memory_some_avg60'))}・full ${fmtPct(v('psi_memory_full_avg60'))}` },
  ]

  return (
    <>
      {header}
      <p className={styles.meta}>
        <Chip mono>{newest.host}</Chip>
        <span>最新觀測 <b>{fmtDateTime(newest.observed_at)}</b>（每 5 分鐘匯入，最多晚一輪）</span>
        <span>粒度 <strong>{RESOLUTION_LABEL[q.data.resolution]}</strong></span>
        {q.data.truncated && <span>資料超過上限，只顯示最近的部分</span>}
      </p>
      <div className={`${c.tiles} ${styles.hostTiles}`}>
        {tiles.map(t => (
          <StatTile key={t.name} name={t.name} label={t.name} value={t.value} unit={t.unit} sub={t.hint} subTitle={t.hint} />
        ))}
      </div>
      {charts.length > 0 && (
        <div className={styles.charts}>
          {charts.map(ch => (
            <Card key={ch.metric} title={ch.title} hint="最近 1 小時" right={<span>最高 <span className={c.num}>{fmtPeak(ch, items)}</span></span>}>
              <div className={styles.chartBody}>
                <TrendBars bars={ch.bars} label={`${ch.title}趨勢`} format={ch.format} max={ch.max} axis={axis} />
              </div>
            </Card>
          ))}
        </div>
      )}
      {filesystems.length > 0 && (
        <Card title="檔案系統" labelledBy="ops-host-fs-title">
          <div className={c.tableWrap}>
            <table className={c.table} aria-label="檔案系統">
              <thead>
                <tr><th>檔案系統</th><th className={c.tdRight}>使用率</th><th className={c.tdRight}>可用</th><th className={c.tdRight}>總量</th></tr>
              </thead>
              <tbody>
                {filesystems.map(([subject, m]) => (
                  <tr key={subject}>
                    <td className={c.mono}>{subject.slice(3)}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{fmtPct(m.get('used_pct')?.value)}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{fmtBytes(m.get('avail_bytes')?.value)}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{fmtBytes(m.get('size_bytes')?.value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  )
}
