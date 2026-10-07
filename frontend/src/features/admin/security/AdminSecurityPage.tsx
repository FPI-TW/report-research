import { RequireAdmin } from '../../../components/shell/RequireAdmin'
import { AdminComingSoon } from '../AdminComingSoon'
import { RequireScope } from '../RequireScope'

/** 安全（/app/admin/security；Admin v2 Security lane）。後端 /api/admin/security/*（audit.read）。 */
export default function AdminSecurityPage() {
  return (
    <RequireAdmin>
      <RequireScope scope="audit.read" title="安全">
        <AdminComingSoon
          title="安全"
          subtitle="登入事件、有效 session 與單一 session 撤銷、高風險操作時間線、稽核鏈狀態與兩步驟驗證採用率。"
        />
      </RequireScope>
    </RequireAdmin>
  )
}
