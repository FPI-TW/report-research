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
import { t } from '@/i18n'

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
    [t('admin.ops.services.serviceDrawer.tier'), `${TIER_LABELS[s.tier]}・${TIER_HINTS[s.tier]}`],
    [s.kind === 'systemd' ? 'unit' : t('admin.ops.services.serviceDrawer.container'), s.target],
    ['ActiveState', stateText(s)],
    ['Result', resultText(s)],
    [t('admin.ops.services.serviceDrawer.lastRun'), fmtDateTime(lastRunAt(s))],
  ]
  if (s.systemd) {
    const d = s.systemd
    rows.push(
      ['LoadState', show(d.load_state)], ['UnitFileState', show(d.unit_file_state)], ['Type', show(d.type)],
      ['MainPID', show(d.main_pid)], [t('admin.ops.services.serviceDrawer.restarts'), show(d.n_restarts)],
      [t('admin.ops.services.serviceDrawer.exitCode'), d.exec_main_code ? t('admin.ops.services.serviceDrawer.text', { exec_main_code: d.exec_main_code, exec_main_status: String(show(d.exec_main_status)) }) : show(d.exec_main_status)],
      [t('admin.ops.services.serviceDrawer.endedAt'), fmtDateTime(d.exec_main_exit_at)], [t('admin.ops.services.serviceDrawer.stateChanged'), fmtDateTime(d.state_change_at)],
    )
  }
  if (s.container) {
    const k = s.container
    rows.push(
      [t('admin.ops.services.serviceDrawer.image'), show(k.image)], [t('admin.ops.services.serviceDrawer.healthCheck'), show(k.health)], [t('admin.ops.services.serviceDrawer.consecutiveFailures'), show(k.failing_streak)],
      [t('admin.ops.services.serviceDrawer.restarts'), show(k.restart_count)], ['OOM', k.oom_killed ? t('admin.ops.services.serviceDrawer.yes') : t('admin.ops.services.serviceDrawer.no')], [t('admin.ops.services.serviceDrawer.exitCode'), show(k.exit_code)],
      [t('admin.ops.services.serviceDrawer.stoppedAt'), fmtDateTime(k.finished_at)], [t('admin.ops.services.serviceDrawer.error'), show(k.error)],
    )
  }
  if (s.timer) {
    rows.push(
      [t('admin.ops.services.serviceDrawer.schedule'), s.timer], [t('admin.ops.services.serviceDrawer.timerState'), show(s.timer_state?.active_state)],
      [t('admin.ops.services.serviceDrawer.lastTrigger'), fmtDateTime(s.timer_state?.last_trigger_at)], [t('admin.ops.services.serviceDrawer.nextTrigger'), fmtDateTime(s.timer_state?.next_elapse_at)],
    )
  } else {
    rows.push([t('admin.ops.services.serviceDrawer.schedule'), t('admin.ops.services.serviceDrawer.longRunning')])
  }
  if (checkedAt) rows.push([t('admin.ops.services.serviceDrawer.checkedAt'), fmtDateTime(checkedAt)])
  return rows
}

function DepChips({ names, lookup }: { names: string[]; lookup: Map<string, OpsDependencyNode> }) {
  const [params] = useSearchParams()
  if (names.length === 0) return <span className={styles.none}>{t('admin.ops.services.serviceDrawer.none')}</span>
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
    if (deps.isPending) return <p className={styles.note}>{t('admin.ops.services.serviceDrawer.loading')}</p>
    return <OpsQueryError error={deps.error} what={t('admin.ops.services.serviceDrawer.dependencies')} />
  }
  const node = deps.nodes.get(name)
  if (!node) return <p className={styles.note}>{t('admin.ops.services.serviceDrawer.thisServiceIsnTIn')}</p>
  return (
    <>
      <div className={styles.depGroup} role="group" aria-label={t('admin.ops.services.serviceDrawer.dependsOn')}>
        <div className={styles.depLabel}>{t('admin.ops.services.serviceDrawer.dependsOn')}</div>
        <DepChips names={node.depends_on} lookup={deps.nodes} />
      </div>
      <div className={styles.depGroup} role="group" aria-label={t('admin.ops.services.serviceDrawer.dependedOnBy')}>
        <div className={styles.depLabel}>{t('admin.ops.services.serviceDrawer.dependedOnBy')}</div>
        <DepChips names={node.dependents} lookup={deps.nodes} />
      </div>
    </>
  )
}

function RecentLogs({ name }: { name: string }) {
  const q = useOpsLogs(name, LOG_SINCE, LOG_LINES)
  if (q.isPending) return <p className={styles.note}>{t('admin.ops.services.serviceDrawer.loading')}</p>
  if (q.isError) return <OpsQueryError error={q.error} what={t('admin.ops.services.serviceDrawer.logs')} />
  if (q.data.entries.length === 0) return <p className={styles.note}>{t('admin.ops.services.serviceDrawer.noLogsInTheLast')}</p>
  return (
    <pre className={`${c.log} ${styles.drawerLog}`} aria-label={t('admin.ops.services.serviceDrawer.recentLogsFor', { name })} tabIndex={0}>
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
        <DrawerSection><OpsQueryError error={q.error} what={t('admin.ops.services.serviceDrawer.service', { name })} /></DrawerSection>
      ) : !s ? (
        <DrawerSection><p className={styles.note}>{t('admin.ops.services.serviceDrawer.loading')}</p></DrawerSection>
      ) : (
        <>
          {s.error && <DrawerSection><p className={c.error} role="alert">{s.error}</p></DrawerSection>}
          <DrawerSection title={t('admin.ops.services.serviceDrawer.properties')}>
            <dl className={`${c.kv} ${styles.kv}`}>
              {attributeRows(s, listed ? checkedAt : q.data?.checked_at)
                .map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}
            </dl>
          </DrawerSection>
          <DrawerSection title={t('admin.ops.services.serviceDrawer.dependencies')}><Dependencies name={name} deps={deps} /></DrawerSection>
          {canLog && (
            <DrawerSection>
              <div className={styles.sectionHead}>
                <span>{t('admin.ops.services.serviceDrawer.recentLogs')}</span>
                <Link className={c.link} to={logsHref(name)}>{t('admin.ops.services.serviceDrawer.openLogsPage')}</Link>
              </div>
              <RecentLogs name={name} />
            </DrawerSection>
          )}
        </>
      )}
    </Drawer>
  )
}
