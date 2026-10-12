import { Suspense, lazy, useEffect } from 'react'
import { createBrowserRouter, Navigate, useLocation, useParams } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { AppShell } from './components/shell/AppShell'
import { RouteError } from './components/shell/RouteError'
import { routeLoaders, preloadIdle } from './lib/routePreload'

const SearchPage = lazy(routeLoaders.search)
const AskPage = lazy(routeLoaders.ask)
const RadarPage = lazy(routeLoaders.radar)
const BriefPage = lazy(routeLoaders.brief)
const HelpPage = lazy(routeLoaders.help)
const ReportPage = lazy(routeLoaders.report)
// 管理後台：外殼 AdminShell 與各頁 default export 都包 RequireAdmin（顯示層守門）；
// 授權在後端 /api/admin/*、/api/review/*。
const AdminShell = lazy(routeLoaders.adminShell)
const AdminOverviewPage = lazy(routeLoaders.adminOverview)
const AdminUsersPage = lazy(routeLoaders.adminUsers)
const AdminReviewsPage = lazy(routeLoaders.adminReviews)
const AdminAuditPage = lazy(routeLoaders.adminAudit)
const AdminReportsPage = lazy(routeLoaders.adminReports)
const AdminUploadsPage = lazy(routeLoaders.adminUploads)
const AdminApiClientsPage = lazy(routeLoaders.adminApiClients)
// 維運（/admin/operations/*）：外殼（ops.read 守門）＋各子頁；jobs／incidents／host 讀 DB 投影。
const OperationsLayout = lazy(routeLoaders.adminOps)
const OpsPipelinePage = lazy(routeLoaders.adminOpsPipeline)
const OpsServicesPage = lazy(routeLoaders.adminOpsServices)
const OpsServiceDetailPage = lazy(routeLoaders.adminOpsService)
const OpsLogsPage = lazy(routeLoaders.adminOpsLogs)
const OpsJobsPage = lazy(routeLoaders.adminOpsJobs)
const OpsIncidentsPage = lazy(routeLoaders.adminOpsIncidents)
const OpsHostPage = lazy(routeLoaders.adminOpsHost)
const OpsDataHealthPage = lazy(routeLoaders.adminOpsDataHealth)
const OpsLlmUsagePage = lazy(routeLoaders.adminOpsLlmUsage)
const OpsDependenciesPage = lazy(routeLoaders.adminOpsDependencies)
const OpsDiagnosticsPage = lazy(routeLoaders.adminOpsDiagnostics)
const OpsDatabasePage = lazy(routeLoaders.adminOpsDatabase)
// Admin v2：分析、安全、配額、功能旗標（各頁自己包 RequireAdmin＋RequireScope；後端各 router 掛 require_admin＋scope）。
const AdminAnalyticsPage = lazy(routeLoaders.adminAnalytics)
const AdminSecurityPage = lazy(routeLoaders.adminSecurity)
const AdminQuotaPage = lazy(routeLoaders.adminQuota)
const AdminFlagsPage = lazy(routeLoaders.adminFlags)

function NotFound() {
  return <div style={{ padding: 20 }}>找不到頁面</div>
}

/** `/admin/ops/*` 是 `/admin/operations/*` 的短別名：子路徑、query 與 hash 原樣帶過去。 */
function OpsAliasRedirect() {
  const rest = useParams()['*'] ?? ''
  const { search, hash } = useLocation()
  return <Navigate to={{ pathname: `/admin/operations${rest ? `/${rest}` : ''}`, search, hash }} replace />
}

/**
 * 舊的詳情頁路徑 → 清單頁＋query：詳情改成抽屜之後，舊連結導到清單頁並把 id 放進 query
 * （例如 `/admin/uploads/:uploadId` → `/admin/uploads?upload=…`）。其他 query 與 hash 原樣保留。
 */
function DetailRedirect({ to, param, query }: { to: string; param: string; query: string }) {
  const value = useParams()[param]
  const { search, hash } = useLocation()
  const next = new URLSearchParams(search)
  if (value) next.set(query, value)
  const qs = next.toString()
  return <Navigate to={{ pathname: to, search: qs ? `?${qs}` : '', hash }} replace />
}

// eslint-disable-next-line react-refresh/only-export-components
export const routes = [
  {
    element: <AppShell />,
    // 外層：連外殼都 render 失敗時的最後一道，自己撐滿視窗。
    errorElement: <RouteError standalone />,
    children: [
      {
        // 內層（無 path 的包裝路由）：頁面壞了只換掉內容區，側欄留著讓人切去別頁。
        // 兩層缺一不可——只掛外層的話，任何一頁出錯都會連導覽一起消失。
        errorElement: <RouteError />,
        children: [
          { path: '/', element: <Navigate to="/search" replace /> },
          { path: '/search', element: <Suspense><SearchPage /></Suspense> },
          { path: '/ask', element: <Suspense><AskPage /></Suspense> },
          // 導入監控已搬進管理後台（/admin/operations/pipeline）；舊書籤多半來自一般使用者，一律回檢索頁。
          { path: '/monitor', element: <Navigate to="/search" replace /> },
          { path: '/radar', element: <Suspense><RadarPage /></Suspense> },
          { path: '/brief', element: <Suspense><BriefPage /></Suspense> },
          { path: '/help', element: <Suspense><HelpPage /></Suspense> },
          // 研報閱讀頁：從檢索進入的詳情頁，不進導覽列（照 /help 慣例）。須排在 '*' 之前。
          { path: '/report/:hash', element: <Suspense><ReportPage /></Suspense> },
          { path: '*', element: <NotFound /> },
        ],
      },
    ],
  },
  {
    // 管理後台：與研報平台完全分開的另一組 layout route（自己的外殼與導覽，沒有研報側欄、
    // 歷史對話）。同一套 SPA、同一個 build；主平台只在帳號選單留管理員入口。
    path: '/admin',
    element: <Suspense><AdminShell /></Suspense>,
    errorElement: <RouteError standalone />,
    children: [
      {
        errorElement: <RouteError />,
        children: [
          { index: true, element: <Suspense><AdminOverviewPage /></Suspense> },
          { path: 'users', element: <Suspense><AdminUsersPage /></Suspense> },
          { path: 'reviews', element: <Suspense><AdminReviewsPage /></Suspense> },
          { path: 'audit', element: <Suspense><AdminAuditPage /></Suspense> },
          { path: 'reports', element: <Suspense><AdminReportsPage /></Suspense> },
          { path: 'uploads', element: <Suspense><AdminUploadsPage /></Suspense> },
          { path: 'uploads/:uploadId', element: <DetailRedirect to="/admin/uploads" param="uploadId" query="upload" /> },
          { path: 'analytics', element: <Suspense><AdminAnalyticsPage /></Suspense> },
          { path: 'security', element: <Suspense><AdminSecurityPage /></Suspense> },
          { path: 'quota', element: <Suspense><AdminQuotaPage /></Suspense> },
          { path: 'flags', element: <Suspense><AdminFlagsPage /></Suspense> },
          { path: 'api-clients', element: <Suspense><AdminApiClientsPage /></Suspense> },
          {
            path: 'operations',
            element: <Suspense><OperationsLayout /></Suspense>,
            children: [
              { index: true, element: <Navigate to="services" replace /> },
              // 維運總覽併進管理後台的總覽（/admin）；舊書籤導過去。
              { path: 'overview', element: <Navigate to="/admin" replace /> },
              { path: 'pipeline', element: <Suspense><OpsPipelinePage /></Suspense> },
              { path: 'services', element: <Suspense><OpsServicesPage /></Suspense> },
              { path: 'services/:name', element: <Suspense><OpsServiceDetailPage /></Suspense> },
              { path: 'dependencies', element: <Suspense><OpsDependenciesPage /></Suspense> },
              { path: 'logs', element: <Suspense><OpsLogsPage /></Suspense> },
              { path: 'jobs', element: <Suspense><OpsJobsPage /></Suspense> },
              { path: 'incidents', element: <Suspense><OpsIncidentsPage /></Suspense> },
              { path: 'host', element: <Suspense><OpsHostPage /></Suspense> },
              { path: 'data-health', element: <Suspense><OpsDataHealthPage /></Suspense> },
              { path: 'llm-usage', element: <Suspense><OpsLlmUsagePage /></Suspense> },
              { path: 'diagnostics', element: <Suspense><OpsDiagnosticsPage /></Suspense> },
              { path: 'database', element: <Suspense><OpsDatabasePage /></Suspense> },
            ],
          },
          { path: 'ops/*', element: <OpsAliasRedirect /> },
          { path: '*', element: <NotFound /> },
        ],
      },
    ],
  },
]

const router = createBrowserRouter(routes, { basename: '/app' })

export default function App() {
  useEffect(() => { preloadIdle(['search', 'ask']) }, [])
  return <RouterProvider router={router} />
}
