import { Fragment, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router'
import { Drawer, DrawerSection, Lamp } from '../../../../components/console'
import c from '../../../../components/console/Console.module.css'
import type { OpsDependencyNode, OpsServiceStatus } from '../../../../lib/generated/adminApi'
import { useHasScope } from '../../../../lib/useMe'
import { fmtDateTime } from '../../auditLabels'
import { OpsServiceActions } from '../OpsServiceActions'
import { OpsQueryError } from '../OpsShared'
import { TIER_HINTS, TIER_LABELS, lastRunAt, resultText, stateText } from '../opsLabels'
import { useOpsLogs, useOpsService } from '../useOps'
import { healthLabel, logsHref, nodeTone } from './health'
import { ServiceStatus } from './ServiceStatus'
import { writeActionsOf } from './useServiceActions'
import styles from './Services.module.css'

const show = (v: unknown): ReactNode => (v == null || v === '' ? '—' : String(v))

/** 抽屜裡「最近日誌」的範圍：後端允許的最小組合（1 小時、100 行），只顯示最後這幾行。 */
const LOG_SINCE = '1h'
const LOG_LINES = 100
const LOG_TAIL = 30

/** 依賴圖的狀態：`nodes` 是以名稱為鍵的節點；還沒載入或載入失敗時是 undefined。 */
export interface DependencyLookup {
  nodes?: Map<string, OpsDependencyNode>
  isPending: boolean
  error: unknown
}

/** systemd 屬性或容器 State 全列出來（原本詳情頁的欄位）。 */
function attributeRows(s: OpsServiceStatus, checkedAt?: string): [string, ReactNode][] {
  const rows: [string, ReactNode][] = [
    ['層級', `${TIER_LABELS[s.tier]}・${TIER_HINTS[s.tier]}`],
    [s.kind === 'systemd' ? 'unit' : '容器', s.target],
    ['ActiveState', stateText(s)],
    ['Result', resultText(s)],
    ['最近執行', fmtDateTime(lastRunAt(s))],
  ]
  if (s.systemd) {
    const d = s.systemd
    rows.push(
      ['LoadState', show(d.load_state)], ['UnitFileState', show(d.unit_file_state)], ['Type', show(d.type)],
      ['MainPID', show(d.main_pid)], ['重啟次數', show(d.n_restarts)],
      ['結束碼', d.exec_main_code ? `${d.exec_main_code}（${show(d.exec_main_status)}）` : show(d.exec_main_status)],
      ['結束時間', fmtDateTime(d.exec_main_exit_at)], ['狀態變更', fmtDateTime(d.state_change_at)],
    )
  }
  if (s.container) {
    const k = s.container
    rows.push(
      ['映像', show(k.image)], ['健康檢查', show(k.health)], ['連續失敗', show(k.failing_streak)],
      ['重啟次數', show(k.restart_count)], ['OOM', k.oom_killed ? '是' : '否'], ['結束碼', show(k.exit_code)],
      ['停止時間', fmtDateTime(k.finished_at)], ['錯誤', show(k.error)],
    )
  }
  if (s.timer) {
    rows.push(
      ['排程', s.timer], ['Timer 狀態', show(s.timer_state?.active_state)],
      ['上次觸發', fmtDateTime(s.timer_state?.last_trigger_at)], ['下次觸發', fmtDateTime(s.timer_state?.next_elapse_at)],
    )
  } else {
    rows.push(['排程', '常駐'])
  }
  if (checkedAt) rows.push(['檢查時間', fmtDateTime(checkedAt)])
  return rows
}

function DepChips({ names, lookup }: { names: string[]; lookup: Map<string, OpsDependencyNode> }) {
  const [params] = useSearchParams()
  if (names.length === 0) return <span className={styles.none}>無</span>
  return (
    <div className={styles.depChips}>
      {names.map(n => {
        const node = lookup.get(n)
        const body = <>{node && <Lamp tone={nodeTone(node)} label={healthLabel(node)} size={5} />}{n}</>
        // 外部依賴（R2、NAS…）沒有服務詳情；其餘換成那個服務的抽屜，篩選等其他 query 留著。
        if (!node || node.kind === 'external') {
          return <span key={n} className={styles.depChip} data-external={node?.kind === 'external' || undefined}>{body}</span>
        }
        const next = new URLSearchParams(params)
        next.set('service', n)
        return <Link key={n} className={styles.depChip} to={`?${next.toString()}`}>{body}</Link>
      })}
    </div>
  )
}

function Dependencies({ name, deps }: { name: string; deps: DependencyLookup }) {
  if (!deps.nodes) {
    if (deps.isPending) return <p className={styles.note}>載入中…</p>
    return <OpsQueryError error={deps.error} what="依賴關係" />
  }
  const node = deps.nodes.get(name)
  if (!node) return <p className={styles.note}>依賴圖裡沒有這個服務。</p>
  return (
    <>
      <div className={styles.depGroup} role="group" aria-label="依賴">
        <div className={styles.depLabel}>依賴</div>
        <DepChips names={node.depends_on} lookup={deps.nodes} />
      </div>
      <div className={styles.depGroup} role="group" aria-label="被依賴">
        <div className={styles.depLabel}>被依賴</div>
        <DepChips names={node.dependents} lookup={deps.nodes} />
      </div>
    </>
  )
}

function RecentLogs({ name }: { name: string }) {
  const q = useOpsLogs(name, LOG_SINCE, LOG_LINES)
  if (q.isPending) return <p className={styles.note}>載入中…</p>
  if (q.isError) return <OpsQueryError error={q.error} what="日誌" />
  if (q.data.entries.length === 0) return <p className={styles.note}>最近 1 小時沒有日誌。</p>
  return (
    <pre className={`${c.log} ${styles.drawerLog}`} aria-label={`${name} 的最近日誌`} tabIndex={0}>
      {q.data.entries.slice(-LOG_TAIL).join('\n')}
    </pre>
  )
}

/**
 * 服務詳情抽屜（470px，`?service=<name>`）：屬性、依賴與被依賴（附燈號）、最近日誌，底部是寫入類操作。
 *
 * 內容用服務清單裡的同一筆（`listed`，跟著清單每 30 秒更新；欄位與單一服務的回應相同）。清單載入完卻沒有
 * 這個名稱時（舊連結、打錯字）才問 `GET /api/admin/ops/services/{name}`，把後端的 404 訊息顯示在抽屜裡。
 */
export function ServiceDrawer({ name, listed, listReady, checkedAt, deps, onClose }: {
  name: string | null
  listed?: OpsServiceStatus
  /** 服務清單已經載入（才知道 `listed` 是「沒有」還是「還沒來」）。 */
  listReady: boolean
  /** 清單的檢查時間。 */
  checkedAt?: string
  deps: DependencyLookup
  onClose: () => void
}) {
  const q = useOpsService(name && listReady && !listed ? name : '')
  const canOperate = useHasScope('ops.operate')
  if (!name) return null
  const s: OpsServiceStatus | undefined = listed ?? q.data
  const failed = !s && q.isError
  const canLog = Boolean(s?.actions.includes('logs'))
  const footer = s && canOperate && writeActionsOf(s).length > 0 ? <OpsServiceActions service={s} /> : undefined
  return (
    <Drawer open onClose={onClose} title={name} monoTitle width={470}
      status={s ? <ServiceStatus service={s} /> : undefined} subtitle={s?.description || undefined} footer={footer}>
      {failed ? (
        <DrawerSection><OpsQueryError error={q.error} what={`服務「${name}」`} /></DrawerSection>
      ) : !s ? (
        <DrawerSection><p className={styles.note}>載入中…</p></DrawerSection>
      ) : (
        <>
          {s.error && <DrawerSection><p className={c.error} role="alert">{s.error}</p></DrawerSection>}
          <DrawerSection title="屬性">
            <dl className={`${c.kv} ${styles.kv}`}>
              {attributeRows(s, listed ? checkedAt : q.data?.checked_at)
                .map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}
            </dl>
          </DrawerSection>
          <DrawerSection title="依賴關係"><Dependencies name={name} deps={deps} /></DrawerSection>
          {canLog && (
            <DrawerSection>
              <div className={styles.sectionHead}>
                <span>最近日誌</span>
                <Link className={c.link} to={logsHref(name)}>開啟日誌頁</Link>
              </div>
              <RecentLogs name={name} />
            </DrawerSection>
          )}
        </>
      )}
    </Drawer>
  )
}
