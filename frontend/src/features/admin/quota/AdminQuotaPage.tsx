import { RequireAdmin } from '../../../components/shell/RequireAdmin'
import { AdminComingSoon } from '../AdminComingSoon'
import { RequireScope } from '../RequireScope'

/** 配額（/app/admin/quota；Admin v2 Quota lane）。後端 /api/admin/quota*（accounts.manage）。 */
export default function AdminQuotaPage() {
  return (
    <RequireAdmin>
      <RequireScope scope="accounts.manage" title="配額">
        <AdminComingSoon
          title="配額"
          subtitle="每人每日的問答、匯出與上傳次數與個人覆寫。正式阻擋前先以影子模式觀察。"
        />
      </RequireScope>
    </RequireAdmin>
  )
}
