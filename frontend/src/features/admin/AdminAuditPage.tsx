import { useState } from 'react'
import { RequireAdmin } from '../../components/shell/RequireAdmin'
import { adminCsvUrls } from '../../lib/generated/adminApi'
import { AdminHeader } from './AdminHeader'
import { ExportCsvButton } from './ExportCsvButton'
import { actionLabel, actorLabel, auditSummary, fmtDateTime } from './auditLabels'
import { AUDIT_PAGE_SIZE, useAdminAudit } from './useAdmin'
import styles from './Admin.module.css'

/** 錯誤物件 → 給人看的訊息。 */
function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '載入失敗，請重試'
}

function AuditLog() {
  const [offset, setOffset] = useState(0)
  const q = useAdminAudit(offset)
  return (
    <section className={styles.card} aria-labelledby="admin-audit-title">
      <div className={styles.cardHead}>
        <h2 id="admin-audit-title" className={styles.ctitle}>最近的操作</h2>
        <ExportCsvButton href={adminCsvUrls.exportAudit()} what="操作紀錄" />
      </div>
      {q.isPending ? (
        <p className={styles.idle}>載入中…</p>
      ) : q.isError ? (
        <p className={styles.error} role="alert">操作紀錄載入失敗：{messageOf(q.error)}</p>
      ) : q.data.items.length === 0 ? (
        <p className={styles.idle}>還沒有任何管理操作</p>
      ) : (
        <>
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead><tr><th>時間</th><th>操作者</th><th>動作</th><th>內容</th></tr></thead>
              <tbody>
                {q.data.items.map(e => (
                  <tr key={e.id}>
                    <td className={styles.num}>{fmtDateTime(e.created_at)}</td>
                    <td className={e.actor_username ? undefined : styles.muted}>{actorLabel(e)}</td>
                    <td>{actionLabel(e.action)}</td>
                    <td>{auditSummary(e)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className={styles.pager}>
            <button type="button" className={styles.action} disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - AUDIT_PAGE_SIZE))}>上一頁</button>
            <span>{`第 ${offset + 1}–${offset + q.data.items.length} 筆，共 ${q.data.total} 筆`}</span>
            <button type="button" className={styles.action} disabled={q.data.next_offset == null}
              onClick={() => { if (q.data.next_offset != null) setOffset(q.data.next_offset) }}>下一頁</button>
          </div>
        </>
      )}
    </section>
  )
}

function AdminAudit() {
  return (
    <div className={styles.page}>
      <div className={styles.inner}>
        <AdminHeader
          title="操作紀錄"
          subtitle="建帳、改角色、停用、重設密碼、強制登出、處理待複核與隱藏研報，誰在何時做了什麼；新的在前。不含任何密碼或註記全文。"
        />
        <AuditLog />
      </div>
    </div>
  )
}

export default function AdminAuditPage() {
  return <RequireAdmin><AdminAudit /></RequireAdmin>
}
