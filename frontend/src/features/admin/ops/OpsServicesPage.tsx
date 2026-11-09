import { Link, useSearchParams } from 'react-router'
import { Chip, PageHeader, RowActions, Segmented, useQueryParam, type RowAction, type SegOption } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type { OpsDependencyNode, OpsServiceStatus } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { OpsQueryError } from './OpsShared'
import { TIER_HINTS, TIER_LABELS, TIERS, lastRunAt, needsAttention, resultText, stateText } from './opsLabels'
import { logsHref } from './services/health'
import { ServiceDrawer, type DependencyLookup } from './services/ServiceDrawer'
import { ServiceStatus } from './services/ServiceStatus'
import { WRITE_ACTION_LABELS, useServiceActions, writeActionsOf, type WriteAction } from './services/useServiceActions'
import { useOpsDependencies, useOpsServices } from './useOps'
import styles from './services/Services.module.css'
import { t } from '@/i18n'

type Filter = 'all' | 'attention' | 'resident' | 'scheduled'

const FILTERS: Record<Filter, { label: string; pass: (s: OpsServiceStatus) => boolean }> = {
  all: { get label() { return t('admin.ops.opsServicesPage.all') }, pass: () => true },
  attention: { get label() { return t('admin.ops.opsServicesPage.needsAttention') }, pass: needsAttention },
  resident: { get label() { return t('admin.ops.opsServicesPage.longRunning') }, pass: s => !s.timer },
  scheduled: { get label() { return t('admin.ops.opsServicesPage.scheduled') }, pass: s => Boolean(s.timer) },
}
const FILTER_KEYS = Object.keys(FILTERS) as Filter[]

const MAX_DEP_CHIPS = 2

const help = () => t('admin.ops.opsServicesPage.serviceStatusDependenciesAndRecent', {
  critical: TIER_HINTS.critical, important: TIER_HINTS.important, supporting: TIER_HINTS.supporting,
})

function TimerCell({ s }: { s: OpsServiceStatus }) {
  if (!s.timer) return <span className={`${c.muted} ${c.nowrap}`}>{t('admin.ops.opsServicesPage.longRunning')}</span>
  const item = s.timer_state
  return (
    <>
      <div className={styles.cellMono} title={t('admin.ops.opsServicesPage.nextTrigger')}>{t('admin.ops.opsServicesPage.next', { next_elapse_at: fmtDateTime(item?.next_elapse_at) })}</div>
      <div className={`${styles.cellMono} ${c.muted}`} title={t('admin.ops.opsServicesPage.lastTrigger')}>{t('admin.ops.opsServicesPage.last', { last_trigger_at: fmtDateTime(item?.last_trigger_at) })}</div>
      {/* timer 沒在跑＝之後不會再觸發：只有這種異常才標出來，完整狀態在抽屜。 */}
      {item?.active_state !== 'active' && (
        <Chip tone="warn" mono title={t('admin.ops.opsServicesPage.timerActivestate')}>timer {item?.active_state ?? '—'}</Chip>
      )}
    </>
  )
}

function DependsOn({ node, deps }: { node?: OpsDependencyNode; deps: DependencyLookup }) {
  if (!deps.nodes) return <span className={c.muted} title={deps.isPending ? t('admin.ops.opsServicesPage.loading') : t('admin.ops.opsServicesPage.couldnTLoadDependencies')}>—</span>
  if (!node || node.depends_on.length === 0) return null
  // 清單裡最多兩個晶片，其餘收成「＋N」（完整清單在 title 與抽屜）：依賴多的服務才不會把表格撐寬。
  const shown = node.depends_on.slice(0, MAX_DEP_CHIPS)
  const rest = node.depends_on.length - shown.length
  return (
    <div className={styles.deps} title={rest > 0 ? node.depends_on.join(t('admin.ops.opsServicesPage.text')) : undefined}>
      {shown.map(n => <Chip key={n} mono title={n}>{n}</Chip>)}
      {rest > 0 && <span className={styles.depsMore}>{t('admin.ops.opsServicesPage.text2', { rest })}</span>}
    </div>
  )
}

/** 核心、重要、輔助三張表共用同一組欄寬（`Services.module.css` 的 `.col`），上下對得齊；第一欄「服務」吃剩下的寬度。 */
const COLS = ['name', 'status', 'state', 'result', 'lastRun', 'timer', 'deps', 'actions'] as const

interface TableProps {
  items: OpsServiceStatus[]
  deps: DependencyLookup
  openName: string | null
  detailHref: (name: string) => string
  onOpen: (name: string) => void
  /** 沒有 `ops.operate` 時是 undefined：寫入類操作不出現在「更多」。 */
  onAction?: (name: string, action: WriteAction) => void
}

function ServiceTable({ items, deps, openName, detailHref, onOpen, onAction }: TableProps) {
  return (
    <div className={c.tableWrap}>
      <table className={`${c.table} ${styles.table}`}>
        <colgroup>{COLS.map(col => <col key={col} className={styles.col} data-col={col} />)}</colgroup>
        <thead>
          <tr>
            <th>{t('admin.ops.opsServicesPage.service')}</th><th>{t('admin.ops.opsServicesPage.status')}</th><th>ActiveState</th><th>Result</th><th>{t('admin.ops.opsServicesPage.lastRun')}</th><th>{t('admin.ops.opsServicesPage.scheduled')}</th><th>{t('admin.ops.opsServicesPage.dependencies')}</th>
            <th className={`${c.tdRight} ${c.stickyRight}`}>{t('admin.ops.opsServicesPage.actions')}</th>
          </tr>
        </thead>
        <tbody>
          {items.map(s => {
            const more: RowAction[] = [
              ...(onAction ? writeActionsOf(s).map(a => ({ label: WRITE_ACTION_LABELS[a], onClick: () => onAction(s.name, a) })) : []),
              { label: t('admin.ops.opsServicesPage.details'), onClick: () => onOpen(s.name) },
            ]
            const rowClass = [c.rowClickable, needsAttention(s) && c.rowBad, openName === s.name && styles.rowOpen]
              .filter(Boolean).join(' ')
            return (
              <tr key={s.name} className={rowClass} onClick={() => onOpen(s.name)}>
                <td>
                  {/* 鍵盤與分享連結的入口；點擊由連結自己處理，不再冒泡到列。 */}
                  <Link className={styles.name} to={detailHref(s.name)} onClick={e => e.stopPropagation()}>{s.name}</Link>
                  <div className={styles.desc} title={s.description || s.target}>{s.description || s.target}</div>
                </td>
                <td>
                  <ServiceStatus service={s} />
                  {s.error && <div className={styles.svcErr}>{s.error}</div>}
                </td>
                <td className={styles.cellMono}>{stateText(s)}</td>
                <td className={styles.cellMono} data-bad={s.summary === 'failed' || undefined}>{resultText(s)}</td>
                <td className={styles.cellMono}>{fmtDateTime(lastRunAt(s))}</td>
                <td><TimerCell s={s} /></td>
                <td><DependsOn node={deps.nodes?.get(s.name)} deps={deps} /></td>
                <td className={`${c.tdActions} ${c.stickyRight}`}>
                  <RowActions label={t('admin.ops.opsServicesPage.moreActionsFor', { name: s.name })}
                    primary={s.actions.includes('logs') ? { label: t('admin.ops.opsServicesPage.logs'), to: logsHref(s.name) } : undefined}
                    more={more} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/**
 * 服務：3 格 tier 摘要＋依 tier 分組（核心／重要／輔助）的表格，每列 ActiveState、Result、最近執行、排程與依賴。
 * 點列開詳情抽屜（`?service=<name>`）；篩選放 `?filter=`。「正常」＝不在 `needsAttention` 裡（排程服務閒置算正常）。
 * 依賴欄與抽屜的依賴關係另外讀依賴圖（`/api/admin/ops/dependencies`），讀不到時那一欄留空，不擋服務清單。
 */
export default function OpsServicesPage() {
  const q = useOpsServices()
  const depsQ = useOpsDependencies()
  const [params, setParams] = useSearchParams()
  const [service, setService] = useQueryParam('service')
  const actions = useServiceActions()

  const raw = params.get('filter')
  const filter: Filter = FILTER_KEYS.includes(raw as Filter) ? (raw as Filter) : 'all'
  const setFilter = (next: Filter) => setParams(prev => {
    const p = new URLSearchParams(prev)
    if (next === 'all') p.delete('filter')
    else p.set('filter', next)
    return p
  }, { replace: true })
  const detailHref = (name: string) => {
    const p = new URLSearchParams(params)
    p.set('service', name)
    return `?${p.toString()}`
  }

  const deps: DependencyLookup = {
    nodes: depsQ.data ? new Map(depsQ.data.nodes.map(n => [n.name, n])) : undefined,
    isPending: depsQ.isPending,
    error: depsQ.error,
  }
  const items = q.data?.items ?? []
  const ok = items.filter(s => !needsAttention(s)).length
  const options: SegOption<Filter>[] = FILTER_KEYS.map(k => ({
    value: k, label: FILTERS[k].label, count: items.filter(FILTERS[k].pass).length,
  }))
  const shown = items.filter(FILTERS[filter].pass)

  return (
    <>
      <PageHeader
        summary={q.data ? t('admin.ops.opsServicesPage.servicesOk', { length: items.length, ok }) : t('admin.ops.opsServicesPage.statusScheduleAndDependenciesOf')}
        help={help()}
        actions={q.data && items.length > 0
          ? <Segmented label={t('admin.ops.opsServicesPage.filterServices')} options={options} value={filter} onChange={setFilter} />
          : undefined}
      />
      {q.isPending ? (
        <p className={c.empty}>{t('admin.ops.opsServicesPage.loading2')}</p>
      ) : q.isError ? (
        <OpsQueryError error={q.error} what={t('admin.ops.opsServicesPage.serviceList')} />
      ) : (
        <>
          {items.length === 0 ? (
            <section className={c.card}><p className={c.empty}>{t('admin.ops.opsServicesPage.theServiceCatalogHasNo')}</p></section>
          ) : (
            <>
              <div className={styles.tiers}>
                {TIERS.map(tier => {
                  const inTier = items.filter(s => s.tier === tier)
                  const bad = inTier.filter(needsAttention).length
                  return (
                    <div key={tier} className={styles.tier} role="group" aria-label={t('admin.ops.opsServicesPage.tier', { TIER_LABELS: TIER_LABELS[tier] })}>
                      <span className={styles.tierCount} data-tone={bad ? 'bad' : undefined}>
                        {t('admin.ops.opsServicesPage.text3', { v: inTier.length - bad, length: inTier.length })}
                      </span>
                      <div className={styles.tierBody}>
                        <div className={styles.tierLabel}>
                          {TIER_LABELS[tier]}
                          <span className={styles.tierState}>{t('common.separator')}{bad ? t('admin.ops.opsServicesPage.needAttention', { bad }) : t('admin.ops.opsServicesPage.allOk')}</span>
                        </div>
                        <div className={styles.tierHint}>{TIER_HINTS[tier]}</div>
                      </div>
                    </div>
                  )
                })}
              </div>
              {shown.length === 0 && (
                <section className={c.card}><p className={c.empty}>{t('admin.ops.opsServicesPage.noServicesMatch', { label: FILTERS[filter].label })}</p></section>
              )}
              {TIERS.map(tier => {
                const inTier = items.filter(s => s.tier === tier)
                const rows = shown.filter(s => s.tier === tier)
                if (rows.length === 0) return null
                return (
                  <section key={tier} className={c.card} aria-label={t('admin.ops.opsServicesPage.services', { TIER_LABELS: TIER_LABELS[tier] })}>
                    <div className={c.cardHead}>
                      <h2 className={c.cardTitle}>{TIER_LABELS[tier]}</h2>
                      <span className={c.cardHint}>{TIER_HINTS[tier]}</span>
                      <span className={`${c.cardRight} ${styles.groupCount}`}>
                        {t('admin.ops.opsServicesPage.ok', { length: inTier.filter(s => !needsAttention(s)).length, length2: inTier.length })}
                      </span>
                    </div>
                    <ServiceTable items={rows} deps={deps} openName={service} detailHref={detailHref}
                      onOpen={setService}
                      onAction={actions.canOperate ? actions.request : undefined} />
                  </section>
                )
              })}
            </>
          )}
          <p className={styles.meta}>
            <span>{q.data.environment}{t('common.separator')}{q.data.host}</span>
            <span>{t('admin.ops.opsServicesPage.checkedAt')}{' '}<b>{fmtDateTime(q.data.checked_at)}</b>{t('admin.ops.opsServicesPage.refreshesEvery30S')}</span>
          </p>
        </>
      )}
      {!q.isError && (
        <ServiceDrawer name={service} listed={items.find(s => s.name === service)} listReady={q.isSuccess}
          checkedAt={q.data?.checked_at} deps={deps} onClose={() => setService(null)} />
      )}
      {actions.dialogs}
    </>
  )
}
