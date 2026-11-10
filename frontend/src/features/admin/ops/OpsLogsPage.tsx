import { useSearchParams } from 'react-router'
import { Button, Card, PageHeader, Segmented, type SegOption } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { fmtDateTime } from '../auditLabels'
import { OpsQueryError } from './OpsShared'
import { useOpsLogs, useOpsServices } from './useOps'
import styles from './exec/Exec.module.css'
import { t } from '@/i18n'

const SINCE_OPTIONS = [
  { value: '15m', get label() { return t('admin.ops.opsLogsPage.last15Minutes') } },
  { value: '1h', get label() { return t('admin.ops.opsLogsPage.last1Hour') } },
  { value: '6h', get label() { return t('admin.ops.opsLogsPage.last6Hours') } },
  { value: '1d', get label() { return t('admin.ops.opsLogsPage.last1Day') } },
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

function LogView({ service, since, lines }: { service: string; since: string; lines: number }) {
  const q = useOpsLogs(service, since, lines)
  if (q.isPending) return <p className={c.empty}>{t('admin.ops.opsLogsPage.loadingLogs')}</p>
  if (q.isError) return <OpsQueryError error={q.error} what={t('admin.ops.opsLogsPage.logs')} />
  const d = q.data
  return (
    <Card
      title={<span className={c.mono}>{d.name}</span>}
      hint={<span className={c.mono}>{d.target}</span>}
      labelledBy="ops-logs-title"
      right={(
        <>
          <span>{t('admin.ops.opsLogsPage.checkedAt', { checked_at: fmtDateTime(d.checked_at) })}</span>
          <Button size="sm" onClick={() => q.refetch()} disabled={q.isFetching}>
            {q.isFetching ? t('admin.ops.opsLogsPage.updating') : t('admin.ops.opsLogsPage.refresh')}
          </Button>
        </>
      )}
    >
      <div className={styles.logBody}>
        <p className={c.meta}>
          <span>{t('admin.ops.opsLogsPage.sinceLines', { since: fmtDateTime(d.since), length: d.entries.length })}</span>
        </p>
        {d.truncated && (
          <p className={c.banner} data-tone="warn" role="note">
            {t('admin.ops.opsLogsPage.theResponseExceededTheLimit')}
          </p>
        )}
        {d.entries.length === 0 ? (
          <p className={c.muted}>{t('admin.ops.opsLogsPage.noLogsInThisPeriod')}</p>
        ) : (
          // tabIndex：限高自己捲的區塊要能用鍵盤捲
          <pre className={`${c.log} ${styles.logBox}`} aria-label={t('admin.ops.opsLogsPage.logsFor', { name: d.name })} tabIndex={0}>{d.entries.join('\n')}</pre>
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
      summary={t('admin.ops.opsLogsPage.recentJournalOrContainerLogs')}
      help={(
        <>
          {t('admin.ops.opsLogsPage.systemdServicesReadTheJournal')}
        </>
      )}
    />
  )

  if (services.isPending) return <>{header}<p className={c.empty}>{t('admin.ops.opsLogsPage.loading')}</p></>
  if (services.isError) return <>{header}<OpsQueryError error={services.error} what={t('admin.ops.opsLogsPage.serviceList')} /></>
  const withLogs = services.data.items.filter(s => s.actions.includes('logs'))
  const service = params.get('service') ?? ''
  const serviceOptions: SegOption<string>[] = withLogs.map(s => ({ value: s.name, label: <span className={c.mono}>{s.name}</span> }))

  return (
    <>
      {header}
      {withLogs.length === 0 ? (
        <p className={c.empty}>{t('admin.ops.opsLogsPage.noServicesAllowViewingLogs')}</p>
      ) : (
        <>
          <div className={styles.pickers}>
            <div className={styles.picker}>
              <span className={styles.pickerLabel}>{t('admin.ops.opsLogsPage.service')}</span>
              {/* 服務在測試環境有三十幾個：分段換行，每個都看得到、一下就切得過去。 */}
              <Segmented wrap label={t('admin.ops.opsLogsPage.service')} options={serviceOptions} value={service} onChange={v => set('service', v)} />
            </div>
            <div className={styles.picker}>
              <span className={styles.pickerLabel}>{t('admin.ops.opsLogsPage.timeRange')}</span>
              <Segmented label={t('admin.ops.opsLogsPage.timeRange')} options={SINCE_OPTIONS} value={since} onChange={v => set('since', v)} />
            </div>
            <div className={styles.picker}>
              <span className={styles.pickerLabel}>{t('admin.ops.opsLogsPage.lines')}</span>
              <Segmented label={t('admin.ops.opsLogsPage.lines')} options={LINE_SEG} value={String(lines)} onChange={v => set('lines', v)} />
            </div>
          </div>
          {service === '' ? (
            <p className={c.empty}>{t('admin.ops.opsLogsPage.chooseAServiceFirst')}</p>
          ) : (
            <LogView service={service} since={since} lines={lines} />
          )}
        </>
      )}
    </>
  )
}
