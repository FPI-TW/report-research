import { useSearchParams } from 'react-router'
import { Button, Card, PageHeader, Segmented, type SegOption } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { fmtDateTime } from '../auditLabels'
import { OpsQueryError } from './OpsShared'
import { useOpsLogs, useOpsServices } from './useOps'
import styles from './exec/Exec.module.css'

const SINCE_OPTIONS = [
  { value: '15m', label: '最近 15 分鐘' },
  { value: '1h', label: '最近 1 小時' },
  { value: '6h', label: '最近 6 小時' },
  { value: '1d', label: '最近 1 天' },
] as const satisfies readonly SegOption<string>[]
const LINE_OPTIONS = [100, 200, 500, 1000] as const
const LINE_SEG: readonly SegOption<string>[] = LINE_OPTIONS.map(n => ({ value: String(n), label: String(n) }))

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

/**
 * 服務的分段切換。共用的 `Segmented` 只有單列；服務在測試環境有三十幾個，所以這裡用同一組樣式
 * 但允許換行，每個服務都看得到、一下就切得過去。
 */
function ServicePicker({ names, value, onChange }: { names: string[]; value: string; onChange: (v: string) => void }) {
  return (
    <div className={`${c.seg} ${styles.segWrap}`} role="group" aria-label="服務">
      {names.map(name => (
        <button key={name} type="button" className={c.segBtn} aria-pressed={name === value} onClick={() => onChange(name)}>
          {name}
        </button>
      ))}
    </div>
  )
}

function LogView({ service, since, lines }: { service: string; since: string; lines: number }) {
  const q = useOpsLogs(service, since, lines)
  if (q.isPending) return <p className={c.empty}>載入日誌中…</p>
  if (q.isError) return <OpsQueryError error={q.error} what="日誌" />
  const d = q.data
  return (
    <Card
      title={<span className={c.mono}>{d.name}</span>}
      hint={<span className={c.mono}>{d.target}</span>}
      labelledBy="ops-logs-title"
      right={(
        <>
          <span>檢查時間 {fmtDateTime(d.checked_at)}</span>
          <Button size="sm" onClick={() => q.refetch()} disabled={q.isFetching}>
            {q.isFetching ? '更新中…' : '重新整理'}
          </Button>
        </>
      )}
    >
      <div className={styles.logBody}>
        <p className={styles.meta}>
          <span>自 {fmtDateTime(d.since)} 起・{d.entries.length} 行</span>
        </p>
        {d.truncated && (
          <p className={c.banner} data-tone="warn" role="note">
            回應超過上限，已從較舊的一端截掉部分內容；要看更早的請縮短時間範圍或減少行數。
          </p>
        )}
        {d.entries.length === 0 ? (
          <p className={c.muted}>這段時間沒有日誌</p>
        ) : (
          // tabIndex：限高自己捲的區塊要能用鍵盤捲
          <pre className={`${c.log} ${styles.logBox}`} aria-label={`${d.name} 的日誌`} tabIndex={0}>{d.entries.join('\n')}</pre>
        )}
      </div>
    </Card>
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
  const header = (
    <PageHeader
      summary="單一服務最近的 journal 或容器日誌；形似祕密的片段已由維運代理遮成 <redacted>。"
      help={(
        <>
          systemd 服務讀 journal、容器讀 docker logs，都經維運代理（唯讀）。回應超過上限時從較舊的一端截掉，
          要看更早的請縮短時間範圍或減少行數。選的服務、時間範圍與行數都放在網址，連結可以直接分享。
        </>
      )}
    />
  )

  if (services.isPending) return <>{header}<p className={c.empty}>載入中…</p></>
  if (services.isError) return <>{header}<OpsQueryError error={services.error} what="服務清單" /></>
  const withLogs = services.data.items.filter(s => s.actions.includes('logs'))
  const service = params.get('service') ?? ''

  return (
    <>
      {header}
      {withLogs.length === 0 ? (
        <p className={c.empty}>沒有開放查看日誌的服務</p>
      ) : (
        <>
          <div className={styles.pickers}>
            <div className={styles.picker}>
              <span className={styles.pickerLabel}>服務</span>
              <ServicePicker names={withLogs.map(s => s.name)} value={service} onChange={v => set('service', v)} />
            </div>
            <div className={styles.picker}>
              <span className={styles.pickerLabel}>時間範圍</span>
              <Segmented label="時間範圍" options={SINCE_OPTIONS} value={since} onChange={v => set('since', v)} />
            </div>
            <div className={styles.picker}>
              <span className={styles.pickerLabel}>行數</span>
              <Segmented label="行數" options={LINE_SEG} value={String(lines)} onChange={v => set('lines', v)} />
            </div>
          </div>
          {service === '' ? (
            <p className={c.empty}>請先選擇服務</p>
          ) : (
            <LogView service={service} since={since} lines={lines} />
          )}
        </>
      )}
    </>
  )
}
