import { useQuery } from '@tanstack/react-query'
import type { AdminCapability } from '../../components/shell/adminNav'
import { adminApi, type OpsServiceListResponse } from '../../lib/generated/adminApi'
import { OPS_KEY } from './ops/useOps'

/**
 * 這台主機少了哪些「頁面要靠它才有資料」的元件。
 *
 * 判斷依據是維運代理的 Service Catalog（`GET /api/admin/ops/services`）：catalog 只列這台實際安裝的 unit
 * （`deploy/ops/services.*.toml`）。監控匯入器 `load-observations` 不在清單裡＝沒裝，主機、排程工作、事件三頁
 * 在這台就永遠是空的——辦公室主機有裝，正式環境（EC2）沒有。
 *
 * 回 null＝不知道（清單還沒回來、維運代理不通、沒有 ops.read）。不知道的時候不要藏頁面。
 */
const MONITORING_LOADER = 'load-observations'

export function missingCapabilities(services: OpsServiceListResponse | undefined): AdminCapability[] | null {
  if (!services) return null
  return services.items.some(s => s.name === MONITORING_LOADER) ? [] : ['monitoring']
}

/**
 * 上一次判斷的結果記在 localStorage（每個網域各一份，也就是每個環境各一份）：下次開後台、服務清單還沒回來之前
 * 就先照它畫導覽，沒裝監控的主機才不會每次都先閃一下那三頁再收掉。讀寫失敗就當沒有。
 */
export const CAPABILITIES_STORAGE_KEY = 'tf.admin.missing'

export function readStoredMissing(): AdminCapability[] {
  try {
    const raw = window.localStorage.getItem(CAPABILITIES_STORAGE_KEY)
    const parsed: unknown = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed.filter((x): x is AdminCapability => x === 'monitoring') : []
  } catch {
    return []
  }
}

export function storeMissing(missing: readonly AdminCapability[]): void {
  try {
    window.localStorage.setItem(CAPABILITIES_STORAGE_KEY, JSON.stringify(missing))
  } catch { /* 存不了就每次重新判斷 */ }
}

/**
 * 頁面裡要知道「這台確定沒裝監控匯入器」時用（導覽與總覽走 `useAdminSignals` 的 `missing`）。
 *
 * 只訂閱外殼已經抓回來的服務清單（`enabled: false`），自己不發請求：讀 DB 投影的頁面刻意不經維運代理，
 * 不為了一句話或一顆按鈕多打一支。清單還沒回來時回 false（當作有裝）。
 */
export function useMonitoringMissing(): boolean {
  const services = useQuery({ queryKey: [...OPS_KEY, 'services'], queryFn: () => adminApi.listOpsServices(), enabled: false })
  return Boolean(missingCapabilities(services.data)?.includes('monitoring'))
}
