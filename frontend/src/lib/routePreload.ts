import type { ComponentType } from 'react'
import { ensureNamespace } from '../i18n'

export type RouteKey =
  | 'search' | 'ask' | 'radar' | 'brief' | 'help' | 'report'
  | 'adminShell' | 'adminOverview' | 'adminUsers' | 'adminReviews' | 'adminAudit' | 'adminReports'
  | 'adminUploads'
  | 'adminOps' | 'adminOpsPipeline' | 'adminOpsServices' | 'adminOpsLogs'
  | 'adminOpsJobs' | 'adminOpsIncidents' | 'adminOpsHost' | 'adminOpsDataHealth' | 'adminOpsLlmUsage'
  | 'adminOpsDependencies'
  | 'adminOpsRemoteHosts'
  | 'adminOpsDiagnostics'
  | 'adminAnalytics' | 'adminSecurity' | 'adminQuota' | 'adminFlags' | 'adminOpsDatabase'
  | 'adminApiClients'

type Loader = () => Promise<{ default: ComponentType }>

/** 管理頁的 chunk 與管理後台的字典（`admin` namespace）一起載入：一般使用者不下載管理後台的字串。 */
export function withAdminMessages(load: Loader): Loader {
  return () => Promise.all([load(), ensureNamespace('admin')]).then(([m]) => m)
}

/** lazy() 與預載共用同一組 import thunk（單一真相，避免路徑字串重複） */
export const routeLoaders: Record<RouteKey, () => Promise<{ default: ComponentType }>> = {
  search: () => import('../features/search/SearchPage'),
  ask: () => import('../features/ask/AskPage'),
  radar: () => import('../features/radar/RadarPage'),
  brief: () => import('../features/brief/BriefPage'),
  help: () => import('../features/help/HelpPage'),
  report: () => import('../features/report/ReportPage'),
  // 管理頁：只有管理員會進來，刻意不放進 App 的閒置預載清單。
  // 外殼也 lazy：一般使用者的瀏覽器連管理後台的版面程式碼都不會下載。
  adminShell: withAdminMessages(() => import('../components/shell/AdminShell').then(m => ({ default: m.AdminShell }))),
  adminOverview: withAdminMessages(() => import('../features/admin/AdminOverviewPage')),
  adminUsers: withAdminMessages(() => import('../features/admin/AdminUsersPage')),
  adminReviews: withAdminMessages(() => import('../features/admin/AdminReviewsPage')),
  adminAudit: withAdminMessages(() => import('../features/admin/AdminAuditPage')),
  adminReports: withAdminMessages(() => import('../features/admin/AdminReportsPage')),
  adminUploads: withAdminMessages(() => import('../features/admin/AdminUploadsPage')),
  adminOps: withAdminMessages(() => import('../features/admin/ops/OperationsLayout')),
  adminOpsPipeline: withAdminMessages(() => import('../features/admin/ops/pipeline/OpsPipelinePage')),
  adminOpsServices: withAdminMessages(() => import('../features/admin/ops/OpsServicesPage')),
  adminOpsLogs: withAdminMessages(() => import('../features/admin/ops/OpsLogsPage')),
  adminOpsJobs: withAdminMessages(() => import('../features/admin/ops/OpsJobsPage')),
  adminOpsIncidents: withAdminMessages(() => import('../features/admin/ops/OpsIncidentsPage')),
  adminOpsHost: withAdminMessages(() => import('../features/admin/ops/OpsHostPage')),
  adminOpsDataHealth: withAdminMessages(() => import('../features/admin/ops/OpsDataHealthPage')),
  adminOpsLlmUsage: withAdminMessages(() => import('../features/admin/ops/OpsLlmUsagePage')),
  adminOpsDependencies: withAdminMessages(() => import('../features/admin/ops/OpsDependenciesPage')),
  adminOpsRemoteHosts: withAdminMessages(() => import('../features/admin/ops/OpsRemoteHostsPage')),
  adminOpsDiagnostics: withAdminMessages(() => import('../features/admin/ops/OpsDiagnosticsPage')),
  // Admin v2（Wave 0 佔位頁；各 lane 只改自己的頁面檔）
  adminAnalytics: withAdminMessages(() => import('../features/admin/analytics/AdminAnalyticsPage')),
  adminSecurity: withAdminMessages(() => import('../features/admin/security/AdminSecurityPage')),
  adminQuota: withAdminMessages(() => import('../features/admin/quota/AdminQuotaPage')),
  adminFlags: withAdminMessages(() => import('../features/admin/flags/AdminFlagsPage')),
  adminOpsDatabase: withAdminMessages(() => import('../features/admin/ops/OpsDatabasePage')),
  adminApiClients: withAdminMessages(() => import('../features/admin/AdminApiClientsPage')),
}

const started = new Set<RouteKey>()

/** 觸發對向路由 chunk 預載；同一 key 只跑一次；失敗時清除狀態以允許之後重試 */
export function preloadRoute(key: RouteKey): void {
  if (started.has(key)) return
  started.add(key)
  routeLoaders[key]().catch(() => {
    started.delete(key)
  })
}

/** 測試用：重置預載狀態，避免測試間互相汙染 */
export function __resetPreloadState(): void {
  started.clear()
}

/** 閒置時批次預載（requestIdleCallback → 退回 setTimeout） */
export function preloadIdle(keys: RouteKey[]): void {
  const run = () => keys.forEach(preloadRoute)
  if (typeof requestIdleCallback === 'function') requestIdleCallback(run)
  else setTimeout(run, 200)
}
