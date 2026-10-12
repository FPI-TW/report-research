import { Fragment, type ReactNode } from 'react'
import { Link, useParams } from 'react-router'
import { fmtDateTime } from '../auditLabels'
import { OpsQueryError, SummaryBadge } from './OpsShared'
import { OpsServiceActions } from './OpsServiceActions'
import { TIER_LABELS, lastRunAt, resultText, stateText } from './opsLabels'
import { useOpsService } from './useOps'
import adminStyles from '../Admin.module.css'
import styles from './Ops.module.css'

const show = (v: unknown): ReactNode => (v == null || v === '' ? '—' : String(v))

/** 單一服務（`GET /api/admin/ops/services/{name}`）：systemd 屬性或容器 State 全列出來。 */
export default function OpsServiceDetailPage() {
  const { name = '' } = useParams()
  const q = useOpsService(name)
  const back = <Link className={adminStyles.action} to=".." relative="path">回到服務清單</Link>
  if (q.isPending) return <p className={adminStyles.idle}>載入中…</p>
  if (q.isError) return <><OpsQueryError error={q.error} what={`服務「${name}」`} /><div>{back}</div></>
  const s = q.data
  const rows: [string, ReactNode][] = [
    ['層級', TIER_LABELS[s.tier]],
    ['類型', s.kind === 'systemd' ? 'systemd' : '容器'],
    ['目標', s.target],
    ['狀態', stateText(s)],
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
    const c = s.container
    rows.push(
      ['映像', show(c.image)], ['健康檢查', show(c.health)], ['連續失敗', show(c.failing_streak)],
      ['重啟次數', show(c.restart_count)], ['OOM', c.oom_killed ? '是' : '否'], ['結束碼', show(c.exit_code)],
      ['停止時間', fmtDateTime(c.finished_at)], ['錯誤', show(c.error)],
    )
  }
  if (s.timer) {
    rows.push(
      ['Timer', s.timer], ['Timer 狀態', show(s.timer_state?.active_state)],
      ['上次觸發', fmtDateTime(s.timer_state?.last_trigger_at)], ['下次觸發', fmtDateTime(s.timer_state?.next_elapse_at)],
    )
  }
  return (
    <section className={adminStyles.card} aria-labelledby="ops-detail-title">
      <h2 id="ops-detail-title" className={adminStyles.ctitle}>{s.name} <SummaryBadge summary={s.summary} /></h2>
      {s.description && <p className={adminStyles.sub}>{s.description}</p>}
      {s.error && <p className={adminStyles.error} role="alert">{s.error}</p>}
      <OpsServiceActions service={s} />
      <div className={adminStyles.spacer} />
      <dl className={styles.dl}>
        {rows.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}
      </dl>
      <p className={adminStyles.hint}>檢查時間 {fmtDateTime(s.checked_at)}</p>
      <div className={styles.links}>
        {back}
        {s.actions.includes('logs') && (
          <Link className={adminStyles.action} to={`../../logs?service=${encodeURIComponent(s.name)}`} relative="path">
            查看日誌
          </Link>
        )}
      </div>
    </section>
  )
}
