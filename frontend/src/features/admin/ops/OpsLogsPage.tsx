import { useSearchParams } from 'react-router'
import { fmtDateTime } from '../auditLabels'
import { OpsQueryError } from './OpsShared'
import { useOpsLogs, useOpsServices } from './useOps'
import adminStyles from '../Admin.module.css'
import styles from './Ops.module.css'

const SINCE_OPTIONS = [
  { value: '15m', label: '最近 15 分鐘' },
  { value: '1h', label: '最近 1 小時' },
  { value: '6h', label: '最近 6 小時' },
  { value: '1d', label: '最近 1 天' },
] as const
const LINE_OPTIONS = [100, 200, 500, 1000] as const

const DEFAULT_SINCE = '1h'
const DEFAULT_LINES = 200

/** 選擇器的值收斂到允許清單；網址被改成別的值時退回預設，不把任意字串送給後端。 */
function pickSince(v: string | null): string {
  return SINCE_OPTIONS.some(o => o.value === v) ? v! : DEFAULT_SINCE
}
function pickLines(v: string | null): number {
  const n = Number(v)
  return (LINE_OPTIONS as readonly number[]).includes(n) ? n : DEFAULT_LINES
}

function LogView({ service, since, lines }: { service: string; since: string; lines: number }) {
  const q = useOpsLogs(service, since, lines)
  if (q.isPending) return <p className={adminStyles.idle}>載入日誌中…</p>
  if (q.isError) return <OpsQueryError error={q.error} what="日誌" />
  const d = q.data
  return (
    <>
      <p className={styles.meta}>
        <span><b>{d.name}</b>（{d.target}）</span>
        <span>自 {fmtDateTime(d.since)} 起・{d.entries.length} 行</span>
        <span>檢查時間 {fmtDateTime(d.checked_at)}</span>
        <button type="button" className={adminStyles.action} onClick={() => q.refetch()} disabled={q.isFetching}>
          {q.isFetching ? '更新中…' : '重新整理'}
        </button>
      </p>
      {d.truncated && (
        <p className={styles.warnNote} role="note">
          回應超過上限，已從較舊的一端截掉部分內容；要看更早的請縮短時間範圍或減少行數。
        </p>
      )}
      {d.entries.length === 0 ? (
        <p className={adminStyles.idle}>這段時間沒有日誌</p>
      ) : (
        <pre className={styles.logBox} aria-label={`${d.name} 的日誌`}>{d.entries.join('\n')}</pre>
      )}
    </>
  )
}

/** 日誌：選服務、時間範圍（15m／1h／6h／1d）與行數；選擇放在網址（可分享、重新整理不丟）。 */
export default function OpsLogsPage() {
  const services = useOpsServices()
  const [params, setParams] = useSearchParams()
  const since = pickSince(params.get('since'))
  const lines = pickLines(params.get('lines'))
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    next.set(key, value)
    setParams(next, { replace: true })
  }

  if (services.isPending) return <p className={adminStyles.idle}>載入中…</p>
  if (services.isError) return <OpsQueryError error={services.error} what="服務清單" />
  const withLogs = services.data.items.filter(s => s.actions.includes('logs'))
  const service = params.get('service') ?? ''

  return (
    <section className={adminStyles.card} aria-labelledby="ops-logs-title">
      <h2 id="ops-logs-title" className={adminStyles.ctitle}>日誌</h2>
      <div className={adminStyles.form}>
        <label className={adminStyles.field}>服務
          <select value={service} onChange={e => set('service', e.target.value)}>
            <option value="" disabled>請選擇服務</option>
            {withLogs.map(s => <option key={s.name} value={s.name}>{s.name}</option>)}
          </select>
        </label>
        <label className={adminStyles.field}>時間範圍
          <select value={since} onChange={e => set('since', e.target.value)}>
            {SINCE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
        <label className={adminStyles.field}>行數
          <select value={lines} onChange={e => set('lines', e.target.value)}>
            {LINE_OPTIONS.map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
      </div>
      {withLogs.length === 0 ? (
        <p className={adminStyles.idle}>沒有開放查看日誌的服務</p>
      ) : service === '' ? (
        <p className={adminStyles.idle}>請先選擇服務</p>
      ) : (
        <LogView service={service} since={since} lines={lines} />
      )}
      <p className={adminStyles.hint}>形似祕密的片段已由維運代理遮成 &lt;redacted&gt;。</p>
    </section>
  )
}
