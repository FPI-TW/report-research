import { NavLink, Outlet } from 'react-router'
import { RequireAdmin } from '../../../components/shell/RequireAdmin'
import { AdminHeader } from '../AdminHeader'
import { RequireScope } from '../RequireScope'
import adminStyles from '../Admin.module.css'
import styles from './Ops.module.css'

// `soon`：API 尚未提供（L3／L4 的 jobs／incidents／observations、P7 的 restart／run-now），先放佔位頁。
const TABS = [
  { to: 'overview', label: '總覽' },
  { to: 'services', label: '服務' },
  { to: 'jobs', label: '排程工作', soon: true },
  { to: 'incidents', label: '事件', soon: true },
  { to: 'logs', label: '日誌' },
  { to: 'host', label: '主機', soon: true },
] as const

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
          subtitle="服務狀態與最近日誌（唯讀），經主機上的維運代理取得。重新啟動、立即執行等操作尚未提供。"
        />
        <nav className={styles.tabs} aria-label="維運子導覽">
          {TABS.map(t => (
            <NavLink key={t.to} to={t.to} className={({ isActive }) => `${styles.tab} ${isActive ? styles.tabOn : ''}`}>
              {t.label}
              {'soon' in t && <span className={styles.soon}>尚未提供</span>}
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
