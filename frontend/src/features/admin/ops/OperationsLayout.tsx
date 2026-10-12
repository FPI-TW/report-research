import { Outlet } from 'react-router'
import { RequireAdmin } from '../../../components/shell/RequireAdmin'
import { AdminHeader } from '../AdminHeader'
import { RequireScope } from '../RequireScope'
import adminStyles from '../Admin.module.css'

// 管線讀 /api/progress（web 行程直接讀 DB 與 log，不經代理）；排程工作、事件與主機讀 DB 投影（/api/admin/jobs、incidents、observations）；服務、依賴圖與日誌經維運代理；
// 資料健康與 LLM 用量讀 /api/admin/data-health、/api/admin/llm-usage（唯讀）；診斷讀 /api/admin/diagnostics；資料庫讀 /api/admin/db/*（系統目錄快照、慢查詢、趨勢）。

/**
 * 維運頁（/app/admin/operations/*）的外殼：標題＋子頁。整組需要 `ops.read`（顯示層；
 * 後端 `/api/admin/ops/*` 每條都 require_scope）。資料一律經 `adminApi`（產生的 client）。
 * 切頁在管理後台外殼的次級面板（`components/shell/adminNav.ts` 的「維運」領域），這裡沒有子導覽。
 */
function Operations() {
  return (
    <div className={adminStyles.page}>
      <div className={adminStyles.inner}>
        <AdminHeader
          title="維運"
          subtitle="管線狀態直接讀 web 行程，維運代理停掉時照常；服務狀態、依賴圖與最近日誌（唯讀）經主機上的維運代理取得；排程工作、事件與主機資源來自監控紀錄（每 5 分鐘匯入）；資料健康彙整批次新鮮度、資料稽核與 R2 對帳，LLM 用量彙整批次的 token。重新啟動 web 與立即執行白名單批次需要「ops.operate」權限並重新驗證密碼。"
        />
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
