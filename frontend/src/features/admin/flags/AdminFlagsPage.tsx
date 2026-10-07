import { RequireAdmin } from '../../../components/shell/RequireAdmin'
import { AdminComingSoon } from '../AdminComingSoon'
import { RequireScope } from '../RequireScope'

/** 功能旗標（/app/admin/flags；Admin v2 Flags lane）。後端 /api/admin/flags*（ops.read；寫入 ops.operate＋重新驗證）。 */
export default function AdminFlagsPage() {
  return (
    <RequireAdmin>
      <RequireScope scope="ops.read" title="功能旗標">
        <AdminComingSoon
          title="功能旗標"
          subtitle="在環境設定允許的範圍內開關派生功能。實際值＝環境上限且旗標開啟；安全相關設定不在這裡。"
        />
      </RequireScope>
    </RequireAdmin>
  )
}
