import { NavLink, Outlet } from 'react-router'
import { RequireAdmin } from '../../../components/shell/RequireAdmin'
import { AdminHeader } from '../AdminHeader'
import { RequireScope } from '../RequireScope'
import adminStyles from '../Admin.module.css'
import styles from './Ops.module.css'

// 排程工作、事件與主機讀 DB 投影（/api/admin/jobs、incidents、observations）；服務與日誌經維運代理。
// 某個分頁的 API 還沒提供時加 `soon: true`，導覽會標「尚未提供」。
const TABS: readonly { to: string; label: string; soon?: boolean }[] = [
  { to: 'overview', label: '總覽' },
  { to: 'services', label: '服務' },
  { to: 'jobs', label: '排程工作' },
  { to: 'incidents', label: '事件' },
  { to: 'logs', label: '日誌' },
  { to: 'host', label: '主機' },
]

/**
 * 維運頁（/app/admin/operations/*）的外殼：標題＋子導覽＋子頁。整組需要 `ops.read`（顯示層；
 * 後端 `/api/admin/ops/*` 每條都 require_scope）。資料一律經 `adminApi`（產生的 client）。
 */
function Operations() {
  return (
    <div className={adminStyles.page}>
      <div className={adminStyles.inner}>
        <AdminHeader
          title="維運"
          subtitle="服務狀態與最近日誌（唯讀）經主機上的維運代理取得；排程工作、事件與主機資源來自監控紀錄（每 5 分鐘匯入）。重新啟動 web 與立即執行白名單批次需要「ops.operate」權限並重新驗證密碼。"
        />
        <nav className={styles.tabs} aria-label="維運子導覽">
          {TABS.map(t => (
            <NavLink key={t.to} to={t.to} className={({ isActive }) => `${styles.tab} ${isActive ? styles.tabOn : ''}`}>
              {t.label}
              {t.soon && <span className={styles.soon}>尚未提供</span>}
            </NavLink>
          ))}
        </nav>
        <Outlet />
      </div>
    </div>
  )
}

export default function OperationsLayout() {
  return (
    <RequireAdmin>
      <RequireScope scope="ops.read" title="維運"><Operations /></RequireScope>
    </RequireAdmin>
  )
}
