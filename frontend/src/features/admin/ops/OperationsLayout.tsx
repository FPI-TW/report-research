import { Outlet } from 'react-router'
import { ConsolePage } from '../../../components/console'
import { RequireAdmin } from '../../../components/shell/RequireAdmin'
import { RequireScope } from '../RequireScope'

// 管線讀 /api/progress（web 行程直接讀 DB 與 log，不經代理）；排程工作、事件與主機讀 DB 投影（/api/admin/jobs、incidents、observations）；服務、依賴圖與日誌經維運代理；
// 資料健康與 LLM 用量讀 /api/admin/data-health、/api/admin/llm-usage（唯讀）；診斷讀 /api/admin/diagnostics；資料庫讀 /api/admin/db/*（系統目錄快照、慢查詢、趨勢）。

/**
 * 維運頁（/app/admin/operations/*）的外殼：ops.read 守門＋主控台的頁面骨架。整組需要 `ops.read`（顯示層；
 * 後端 `/api/admin/ops/*` 每條都 require_scope）。資料一律經 `adminApi`（產生的 client）。
 * 切頁在管理後台外殼的次級面板（`components/shell/adminNav.ts` 的「維運」領域）；頁首由各子頁自己放
 * `PageHeader`（`components/console`），這裡沒有標題也沒有子導覽。
 */
export default function OperationsLayout() {
  return (
    <RequireAdmin>
      <RequireScope scope="ops.read" title="維運">
        <ConsolePage><Outlet /></ConsolePage>
      </RequireScope>
    </RequireAdmin>
  )
}
