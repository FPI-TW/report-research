import type { ComponentType } from 'react'

export type RouteKey =
  | 'search' | 'ask' | 'radar' | 'brief' | 'help' | 'report'
  | 'adminShell' | 'adminUsers' | 'adminReviews' | 'adminAudit' | 'adminReports'
  | 'adminUploads' | 'adminUpload'
  | 'adminOps' | 'adminOpsOverview' | 'adminOpsPipeline' | 'adminOpsServices' | 'adminOpsService' | 'adminOpsLogs'
  | 'adminOpsJobs' | 'adminOpsIncidents' | 'adminOpsHost' | 'adminOpsDataHealth' | 'adminOpsLlmUsage'
  | 'adminOpsDependencies'
  | 'adminOpsDiagnostics'
  | 'adminAnalytics' | 'adminSecurity' | 'adminQuota' | 'adminFlags' | 'adminOpsDatabase'

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
  adminShell: () => import('../components/shell/AdminShell').then(m => ({ default: m.AdminShell })),
  adminUsers: () => import('../features/admin/AdminUsersPage'),
  adminReviews: () => import('../features/admin/AdminReviewsPage'),
  adminAudit: () => import('../features/admin/AdminAuditPage'),
  adminReports: () => import('../features/admin/AdminReportsPage'),
  adminUploads: () => import('../features/admin/AdminUploadsPage'),
  adminUpload: () => import('../features/admin/AdminUploadDetailPage'),
  adminOps: () => import('../features/admin/ops/OperationsLayout'),
  adminOpsOverview: () => import('../features/admin/ops/OpsOverviewPage'),
  adminOpsPipeline: () => import('../features/admin/ops/pipeline/OpsPipelinePage'),
  adminOpsServices: () => import('../features/admin/ops/OpsServicesPage'),
  adminOpsService: () => import('../features/admin/ops/OpsServiceDetailPage'),
  adminOpsLogs: () => import('../features/admin/ops/OpsLogsPage'),
  adminOpsJobs: () => import('../features/admin/ops/OpsJobsPage'),
  adminOpsIncidents: () => import('../features/admin/ops/OpsIncidentsPage'),
  adminOpsHost: () => import('../features/admin/ops/OpsHostPage'),
  adminOpsDataHealth: () => import('../features/admin/ops/OpsDataHealthPage'),
  adminOpsLlmUsage: () => import('../features/admin/ops/OpsLlmUsagePage'),
  adminOpsDependencies: () => import('../features/admin/ops/OpsDependenciesPage'),
  adminOpsDiagnostics: () => import('../features/admin/ops/OpsDiagnosticsPage'),
  // Admin v2（Wave 0 佔位頁；各 lane 只改自己的頁面檔）
  adminAnalytics: () => import('../features/admin/analytics/AdminAnalyticsPage'),
  adminSecurity: () => import('../features/admin/security/AdminSecurityPage'),
  adminQuota: () => import('../features/admin/quota/AdminQuotaPage'),
  adminFlags: () => import('../features/admin/flags/AdminFlagsPage'),
  adminOpsDatabase: () => import('../features/admin/ops/OpsDatabasePage'),
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
