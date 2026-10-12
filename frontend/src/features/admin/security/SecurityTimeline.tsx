import { useState } from 'react'
import { actionLabel, actorLabel, auditSummary, fmtDateTime } from '../auditLabels'
import adminStyles from '../Admin.module.css'
import styles from './Security.module.css'
import { CATEGORY_LABELS } from './securityLabels'
import { type HighRiskCategory, useHighRisk } from './useSecurity'

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '載入失敗，請重試'
}

const DAYS = [7, 30, 90, 365] as const

/** 高風險操作時間線（稽核紀錄裡屬於 security_ops.HIGH_RISK_ACTIONS 的列）。只顯示，不送告警。 */
export function HighRiskCard() {
  const [days, setDays] = useState<number>(30)
  const [category, setCategory] = useState<HighRiskCategory | ''>('')
  const [cursors, setCursors] = useState<(number | null)[]>([null])
  const q = useHighRisk({ days, category, beforeId: cursors[cursors.length - 1] })

  return (
    <section className={adminStyles.card} aria-labelledby="sec-risk-title">
      <h2 id="sec-risk-title" className={adminStyles.ctitle}>高風險操作</h2>
      <div className={styles.filters}>
        <label className={adminStyles.field}>
          分類
          <select value={category} onChange={e => { setCategory(e.target.value as HighRiskCategory | ''); setCursors([null]) }}>
            <option value="">全部</option>
            {Object.entries(CATEGORY_LABELS).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
        </label>
        <label className={adminStyles.field}>
          期間
          <select value={days} onChange={e => { setDays(Number(e.target.value)); setCursors([null]) }}>
            {DAYS.map(d => <option key={d} value={d}>{`${d} 天`}</option>)}
          </select>
        </label>
      </div>
      {q.isPending ? (
        <p className={adminStyles.idle}>載入中…</p>
      ) : q.isError ? (
        <p className={adminStyles.error} role="alert">高風險操作載入失敗：{messageOf(q.error)}</p>
      ) : q.data.items.length === 0 ? (
        <p className={adminStyles.idle}>這段期間沒有高風險操作</p>
      ) : (
        <>
          <div className={adminStyles.tableWrap}>
            <table className={adminStyles.table}>
              <thead><tr><th>時間</th><th>分類</th><th>操作者</th><th>動作</th><th>內容</th></tr></thead>
              <tbody>
                {q.data.items.map(e => (
                  <tr key={e.id}>
                    <td className={adminStyles.num}>{fmtDateTime(e.created_at)}</td>
                    <td>{CATEGORY_LABELS[e.category] ?? e.category}</td>
                    <td className={e.actor_username ? undefined : adminStyles.muted}>
                      {actorLabel({ ...e, actor_user_id: e.actor_user_id ?? null, actor_username: e.actor_username ?? null,
                        target_id: e.target_id ?? null })}
                    </td>
                    <td>{actionLabel(e.action)}</td>
                    <td className={adminStyles.wrapCell}>
                      {auditSummary({ ...e, actor_user_id: e.actor_user_id ?? null, actor_username: e.actor_username ?? null,
                        target_id: e.target_id ?? null })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className={adminStyles.pager}>
            <button type="button" className={adminStyles.action} disabled={cursors.length <= 1}
              onClick={() => setCursors(cursors.slice(0, -1))}>較新</button>
            <span>{`第 ${cursors.length} 頁`}</span>
            <button type="button" className={adminStyles.action} disabled={q.data.next_before_id == null}
              onClick={() => { if (q.data.next_before_id != null) setCursors([...cursors, q.data.next_before_id]) }}>
              較舊
            </button>
          </div>
        </>
      )}
    </section>
  )
}
