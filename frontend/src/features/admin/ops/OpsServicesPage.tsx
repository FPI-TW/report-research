import { Link, useNavigate, useSearchParams } from 'react-router'
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

type Filter = 'all' | 'attention' | 'resident' | 'scheduled'

const FILTERS: Record<Filter, { label: string; pass: (s: OpsServiceStatus) => boolean }> = {
  all: { label: '全部', pass: () => true },
  attention: { label: '需要注意', pass: needsAttention },
  resident: { label: '常駐', pass: s => !s.timer },
  scheduled: { label: '排程', pass: s => Boolean(s.timer) },
}
const FILTER_KEYS = Object.keys(FILTERS) as Filter[]

const HELP = (
  <>
    服務狀態、依賴關係與最近日誌（唯讀）經主機上的維運代理取得，代理不可用時研報平台的其他功能不受影響。
    核心＝{TIER_HINTS.critical}；重要＝{TIER_HINTS.important}；輔助＝{TIER_HINTS.supporting}。
    排程類服務在兩次執行之間會顯示閒置，算正常；核心服務沒在跑才算需要注意。
    重新啟動 web 與立即執行白名單批次需要「ops.operate」權限並重新驗證。
  </>
)

function TimerCell({ s }: { s: OpsServiceStatus }) {
  if (!s.timer) return <span className={c.muted}>常駐</span>
  const t = s.timer_state
  return (
    <>
      <div className={styles.cellMono} title="下次觸發">下次 {fmtDateTime(t?.next_elapse_at)}</div>
      <div className={`${styles.cellMono} ${c.muted}`} title="上次觸發">上次 {fmtDateTime(t?.last_trigger_at)}</div>
      {/* timer 沒在跑＝之後不會再觸發：只有這種異常才標出來，完整狀態在抽屜。 */}
      {t?.active_state !== 'active' && (
        <Chip tone="warn" mono title="timer 的 ActiveState">timer {t?.active_state ?? '—'}</Chip>
      )}
    </>
  )
}

function DependsOn({ node, deps }: { node?: OpsDependencyNode; deps: DependencyLookup }) {
  if (!deps.nodes) return <span className={c.muted} title={deps.isPending ? '載入中' : '依賴關係載入失敗'}>—</span>
  if (!node || node.depends_on.length === 0) return null
  return <div className={styles.deps}>{node.depends_on.map(n => <Chip key={n} mono>{n}</Chip>)}</div>
}

interface TableProps {
  items: OpsServiceStatus[]
  deps: DependencyLookup
  openName: string | null
  detailHref: (name: string) => string
  onOpen: (name: string) => void
  onLogs: (name: string) => void
  /** 沒有 `ops.operate` 時是 undefined：寫入類操作不出現在「更多」。 */
  onAction?: (name: string, action: WriteAction) => void
}

function ServiceTable({ items, deps, openName, detailHref, onOpen, onLogs, onAction }: TableProps) {
  return (
    <div className={c.tableWrap}>
      <table className={c.table}>
        <thead>
          <tr>
            <th>服務</th><th>狀態</th><th>ActiveState</th><th>Result</th><th>最近執行</th><th>排程</th><th>依賴</th>
            <th className={c.tdRight}>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.map(s => {
            const more: RowAction[] = [
              ...(onAction ? writeActionsOf(s).map(a => ({ label: WRITE_ACTION_LABELS[a], onClick: () => onAction(s.name, a) })) : []),
              { label: '詳情', onClick: () => onOpen(s.name) },
            ]
            const rowClass = [c.rowClickable, needsAttention(s) && c.rowBad, openName === s.name && styles.rowOpen]
              .filter(Boolean).join(' ')
            return (
              <tr key={s.name} className={rowClass} onClick={() => onOpen(s.name)}>
                <td className={styles.nameCell}>
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
                <td className={c.tdActions}>
                  <RowActions label={`${s.name} 的更多操作`}
                    primary={s.actions.includes('logs') ? { label: '日誌', onClick: () => onLogs(s.name) } : undefined}
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
  const navigate = useNavigate()
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
        summary={q.data ? `${items.length} 個服務，${ok} 個正常。` : '各服務的狀態、排程與依賴。'}
        help={HELP}
        actions={q.data && items.length > 0
          ? <Segmented label="篩選服務" options={options} value={filter} onChange={setFilter} />
          : undefined}
      />
      {q.isPending ? (
        <p className={c.empty}>載入中…</p>
      ) : q.isError ? (
        <OpsQueryError error={q.error} what="服務清單" />
      ) : (
        <>
          {items.length === 0 ? (
            <section className={c.card}><p className={c.empty}>Service Catalog 裡沒有任何服務</p></section>
          ) : (
            <>
              <div className={styles.tiers}>
                {TIERS.map(tier => {
                  const inTier = items.filter(s => s.tier === tier)
                  const bad = inTier.filter(needsAttention).length
                  return (
                    <div key={tier} className={styles.tier} role="group" aria-label={`${TIER_LABELS[tier]}層級`}>
                      <span className={styles.tierCount} data-tone={bad ? 'bad' : undefined}>
                        {inTier.length - bad}／{inTier.length}
                      </span>
                      <div className={styles.tierBody}>
                        <div className={styles.tierLabel}>
                          {TIER_LABELS[tier]}
                          <span className={styles.tierState}>・{bad ? `${bad} 個需要注意` : '全部正常'}</span>
                        </div>
                        <div className={styles.tierHint}>{TIER_HINTS[tier]}</div>
                      </div>
                    </div>
                  )
                })}
              </div>
              {shown.length === 0 && (
                <section className={c.card}><p className={c.empty}>沒有符合「{FILTERS[filter].label}」的服務</p></section>
              )}
              {TIERS.map(tier => {
                const inTier = items.filter(s => s.tier === tier)
                const rows = shown.filter(s => s.tier === tier)
                if (rows.length === 0) return null
                return (
                  <section key={tier} className={c.card} aria-label={`${TIER_LABELS[tier]}服務`}>
                    <div className={c.cardHead}>
                      <h2 className={c.cardTitle}>{TIER_LABELS[tier]}</h2>
                      <span className={c.cardHint}>{TIER_HINTS[tier]}</span>
                      <span className={`${c.cardRight} ${styles.groupCount}`}>
                        {inTier.filter(s => !needsAttention(s)).length}／{inTier.length} 正常
                      </span>
                    </div>
                    <ServiceTable items={rows} deps={deps} openName={service} detailHref={detailHref}
                      onOpen={setService} onLogs={name => { void navigate(logsHref(name)) }}
                      onAction={actions.canOperate ? actions.request : undefined} />
                  </section>
                )
              })}
            </>
          )}
          <p className={styles.meta}>
            <span>{q.data.environment}・{q.data.host}</span>
            <span>檢查時間 <b>{fmtDateTime(q.data.checked_at)}</b>（每 30 秒更新）</span>
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
