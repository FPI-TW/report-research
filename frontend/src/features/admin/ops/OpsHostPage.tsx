import { useMemo } from 'react'
import { useSearchParams } from 'react-router'
import type { ObservationItem, ObservationListResponse } from '../../../lib/generated/adminApi'
import {
  Card, Chip, PageHeader, Segmented, StatTile, TrendBars, type SegOption, type TrendBar,
} from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { fmtDateTime } from '../auditLabels'
import { peak, trendAxis, trendSeries, trendSlotSpan } from './exec/hostSeries'
import { OpsQueryError } from './OpsShared'
import { useHostObservations, useHostTrends, type HostSeriesKey, type HostTrendRange } from './useOps'
import styles from './exec/Exec.module.css'

type Latest = Map<string, Map<string, ObservationItem>>

/** 後端依查詢區間選的粒度（24 小時內逐筆，更早的是 5 分鐘／1 小時聚合，見 revision 0007）。 */
const RESOLUTION_LABEL: Record<ObservationListResponse['resolution'], string> = {
  raw: '原始觀測（每 60 秒）',
  '5m': '5 分鐘聚合',
  '1h': '1 小時聚合',
}

type HostRange = '1h' | HostTrendRange

const RANGE_OPTIONS: readonly SegOption<HostRange>[] = [
  { value: '1h', label: '1 小時' },
  { value: '24h', label: '24 小時' },
  { value: '7d', label: '7 天' },
]
const RANGE_LABEL: Record<HostRange, string> = { '1h': '最近 1 小時', '24h': '最近 24 小時', '7d': '最近 7 天' }
const DEFAULT_RANGE: HostRange = '1h'

/** 網址的 `range` 不在選項內就退回預設值。 */
function pickRange(v: string | null): HostRange {
  return RANGE_OPTIONS.find(o => o.value === v)?.value ?? DEFAULT_RANGE
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
const HOST_SERIES: readonly HostSeriesKey[] = CHARTS.map(ch => ({ subject: 'host', metric: ch.metric }))
/** 檔案系統的使用率（收集器對每個掛載點寫一個 `fs:<路徑>` 的 subject）。 */
const FS_METRIC = 'used_pct'

/** 一張趨勢圖的資料來源：1 小時用數字磚那一份，其餘範圍是該序列自己的查詢。 */
interface TrendSource { data: ObservationListResponse | undefined; isPending: boolean; error: unknown }

const seriesId = (s: HostSeriesKey) => `${s.subject}\n${s.metric}`

interface TrendView {
  series: HostSeriesKey
  src: TrendSource
  /** 切好格的柱子；資料還沒到（載入中、失敗）時是 null。 */
  bars: TrendBar[] | null
  format: (v: number) => string
}

function trendView(series: HostSeriesKey, src: TrendSource, format: (v: number) => string): TrendView {
  return { series, src, format, bars: src.data ? trendSeries(src.data, series.subject, series.metric) : null }
}

/** 有沒有東西可畫：還在載入或載入失敗也算（要顯示狀態），載入完卻沒有任何觀測的序列不畫。 */
function drawable(view: TrendView): boolean {
  return view.bars == null || view.bars.some(b => b.value != null)
}

function fmtSeriesPeak({ src, series, format }: TrendView): string {
  const p = src.data ? peak(src.data.items, series.subject, series.metric) : null
  return p == null ? '—' : format(p)
}

/** 卡片標題旁的說明：範圍，資料到了再補上一根柱子涵蓋多久。 */
function trendHint(range: HostRange, src: TrendSource | undefined): string {
  const span = src?.data ? trendSlotSpan(src.data) : null
  return span ? `${RANGE_LABEL[range]}・每根 ${span}` : RANGE_LABEL[range]
}

/** 一條序列的趨勢圖本體（卡片內容）：載入中、載入失敗、圖；該序列自己的查詢被截斷時講明。 */
function TrendPlot({ view, label, max, ownQuery }: { view: TrendView; label: string; max?: number; ownQuery: boolean }) {
  const { src, bars } = view
  if (src.isPending) return <p className={c.empty}>載入中…</p>
  if (!src.data || !bars) return <div className={styles.chartBody}><OpsQueryError error={src.error} what={label} /></div>
  return (
    <div className={styles.chartBody}>
      <TrendBars bars={bars} label={label} format={view.format} max={max} axis={trendAxis(src.data)} />
      {ownQuery && src.data.truncated && <p className={styles.truncated}>資料超過上限，較早的部分沒有畫出來</p>}
    </div>
  )
}

/**
 * 主機：最近一小時的主機觀測（CPU、記憶體、磁碟、I/O PSI、load）取最新值，CPU／記憶體／I/O 壓力另附一小時
 * 最高值；趨勢圖可切 1 小時、24 小時、7 天（`?range=`），7 天另附各檔案系統的使用率；下方是檔案系統用量。
 * 資料來自 DB 投影（收集器每 60 秒寫 spool、loader 每 5 分鐘匯入），不經維運代理。
 */
export default function OpsHostPage() {
  const [params, setParams] = useSearchParams()
  const range = pickRange(params.get('range'))
  const setRange = (next: HostRange) => {
    const p = new URLSearchParams(params)
    if (next === DEFAULT_RANGE) p.delete('range')
    else p.set('range', next)
    setParams(p, { replace: true })
  }
  const q = useHostObservations()
  // 檔案系統有哪些，看最近一小時的觀測；key 是字串，每分鐘重抓回同一批掛載點時下面的序列不會換一份。
  const fsKey = (q.data?.items ?? []).filter(it => it.subject.startsWith('fs:')).map(it => it.subject)
    .filter((s, i, all) => all.indexOf(s) === i).join('\n')
  const fsSeries = useMemo<HostSeriesKey[]>(
    () => (fsKey ? fsKey.split('\n').map(subject => ({ subject, metric: FS_METRIC })) : []), [fsKey])
  const wanted = useMemo<HostSeriesKey[]>(
    () => (range === '7d' ? [...HOST_SERIES, ...fsSeries] : range === '24h' ? [...HOST_SERIES] : []), [range, fsSeries])
  // 數字磚那一份成功了才查趨勢：它失敗時頁面只顯示那一個錯誤，不再多送幾個多半也會失敗的請求。
  const trends = useHostTrends(range === '1h' || !q.isSuccess ? null : range, wanted)

  const header = (
    <PageHeader
      summary="主機與檔案系統的資源用量：數字取最近一小時的觀測，趨勢可看 1 小時、24 小時、7 天；每 5 分鐘匯入，最多晚一輪。"
      help={(
        <>
          數字磚是最新一筆觀測，CPU 與 I/O 壓力另附這一小時的最高值，不隨時間範圍改變；趨勢圖每根柱子是一段時間內的最高值，
          沒有觀測的區間留空。24 小時內畫的是每 60 秒一筆的原始觀測，更早的是 5 分鐘聚合；7 天另附各檔案系統的使用率。
          資料來自 DB 投影（主機上的收集器每 60 秒寫 spool、匯入器每 5 分鐘匯入一次），不經維運代理，所以代理停掉時這頁照常。
        </>
      )}
      actions={<Segmented label="時間範圍" options={RANGE_OPTIONS} value={range} onChange={setRange} />}
    />
  )
  if (q.isPending) return <>{header}<p className={c.empty}>載入中…</p></>
  if (q.isError) return <>{header}<OpsQueryError error={q.error} what="主機觀測" /></>

  const items = q.data.items
  const latest = latestBySubject(items)
  const host = latest.get('host')
  const v = (metric: string) => host?.get(metric)?.value ?? null
  const newest = items.at(0)
  const filesystems = [...latest.entries()].filter(([subject]) => subject.startsWith('fs:'))

  const ownQuery = range !== '1h'
  const bySeries = new Map(wanted.map((s, i) => [seriesId(s), trends[i]]))
  const source = (s: HostSeriesKey): TrendSource => {
    if (!ownQuery) return { data: q.data, isPending: false, error: null }
    const t = bySeries.get(seriesId(s))
    return { data: t?.data, isPending: t?.isPending ?? true, error: t?.error ?? null }
  }
  const charts = CHARTS.map(ch => {
    const series: HostSeriesKey = { subject: 'host', metric: ch.metric }
    return { ...ch, view: trendView(series, source(series), ch.format) }
  }).filter(ch => drawable(ch.view))
  const fsCharts = (range === '7d' ? fsSeries : []).map(series => trendView(series, source(series), fmtPct))
    .filter(drawable)
  // 同一個範圍的查詢 since 相同，粒度也就相同：取第一份到的。
  const resolution = ownQuery
    ? [...charts.map(ch => ch.view), ...fsCharts].find(view => view.src.data)?.src.data?.resolution
    : q.data.resolution
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
      {newest ? (
        <>
          <p className={c.meta}>
            <Chip mono>{newest.host}</Chip>
            <span>最新觀測 <b>{fmtDateTime(newest.observed_at)}</b>（每 5 分鐘匯入，最多晚一輪）</span>
            {resolution && <span>粒度 <strong className={styles.metaStrong}>{RESOLUTION_LABEL[resolution]}</strong></span>}
            {q.data.truncated && <span>資料超過上限，只顯示最近的部分</span>}
          </p>
          <div className={`${c.tiles} ${styles.hostTiles}`}>
            {tiles.map(t => (
              <StatTile key={t.name} name={t.name} label={t.name} value={t.value} unit={t.unit} sub={t.hint} subTitle={t.hint} />
            ))}
          </div>
        </>
      ) : (
        <p className={c.empty}>
          最近一小時沒有主機觀測。確認主機上的收集器（report-mark-metrics）與匯入器
          （report-mark-load-observations.timer）都在跑；資料每 5 分鐘匯入一次。
        </p>
      )}
      {charts.length > 0 && (
        <div className={styles.charts}>
          {charts.map(ch => (
            <Card key={ch.metric} title={ch.title} hint={trendHint(range, ch.view.src)}
              right={<span>最高 <span className={c.num}>{fmtSeriesPeak(ch.view)}</span></span>}>
              <TrendPlot view={ch.view} label={`${ch.title}趨勢`} max={ch.max} ownQuery={ownQuery} />
            </Card>
          ))}
        </div>
      )}
      {fsCharts.length > 0 && (
        <Card title="檔案系統使用率" hint={trendHint(range, fsCharts.find(f => f.src.data)?.src)}
          labelledBy="ops-host-fs-trend-title">
          {fsCharts.map(f => {
            const path = f.series.subject.slice(3)
            return (
              <div key={f.series.subject} className={styles.fsTrend}>
                <div className={styles.fsTrendHead}>
                  <span className={c.mono}>{path}</span>
                  <span>最高 <span className={c.num}>{fmtSeriesPeak(f)}</span></span>
                </div>
                <TrendPlot view={f} label={`${path} 使用率趨勢`} max={100} ownQuery />
              </div>
            )
          })}
        </Card>
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
