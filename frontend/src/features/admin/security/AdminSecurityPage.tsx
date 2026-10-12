import { useState } from 'react'
import { RequireAdmin } from '../../../components/shell/RequireAdmin'
import { AdminHeader } from '../AdminHeader'
import { RequireScope } from '../RequireScope'
import adminStyles from '../Admin.module.css'
import styles from './Security.module.css'
import { AuthEventsCard, SuspiciousIpsCard } from './SecurityEvents'
import { AuditChainCard, SecurityAlerts, TotpAdoptionCard } from './SecurityOverview'
import { SessionsCard } from './SecuritySessions'
import { HighRiskCard } from './SecurityTimeline'

function AdminSecurity() {
  // 可疑 IP 的「看事件」把 IP 帶進登入事件的篩選。
  const [ipFilter, setIpFilter] = useState('')
  return (
    <div className={adminStyles.page}>
      <div className={adminStyles.inner}>
        <AdminHeader
          title="安全"
          subtitle="登入事件、可疑 IP、有效 session 與單一 session 撤銷、高風險操作時間線、稽核鏈與錨定狀態、兩步驟驗證採用率。登入事件不存帳號名稱；本站不鎖帳號、不封 IP。"
        />
        <SecurityAlerts />
        <div className={styles.grid}>
          <AuditChainCard />
          <TotpAdoptionCard />
        </div>
        <SuspiciousIpsCard onPick={ip => setIpFilter(ip)} />
        <AuthEventsCard ipFilter={ipFilter} onIpFilter={setIpFilter} />
        <SessionsCard />
        <HighRiskCard />
      </div>
    </div>
  )
}

/** 安全（/app/admin/security；Admin v2 Security lane）。後端 /api/admin/security/*（audit.read；session 另要 accounts.manage）。 */
export default function AdminSecurityPage() {
  return (
    <RequireAdmin>
      <RequireScope scope="audit.read" title="安全">
        <AdminSecurity />
      </RequireScope>
    </RequireAdmin>
  )
}
