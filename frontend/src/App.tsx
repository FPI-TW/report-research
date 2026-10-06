import { Suspense, lazy, useEffect } from 'react'
import { createBrowserRouter, Navigate } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { AppShell } from './components/shell/AppShell'
import { RouteError } from './components/shell/RouteError'
import { routeLoaders, preloadIdle } from './lib/routePreload'

const SearchPage = lazy(routeLoaders.search)
const AskPage = lazy(routeLoaders.ask)
const MonitorPage = lazy(routeLoaders.monitor)
const RadarPage = lazy(routeLoaders.radar)
const BriefPage = lazy(routeLoaders.brief)
const HelpPage = lazy(routeLoaders.help)
const ReportPage = lazy(routeLoaders.report)
// 管理後台：外殼 AdminShell 與各頁 default export 都包 RequireAdmin（顯示層守門）；
// 授權在後端 /api/admin/*、/api/review/*。
const AdminShell = lazy(routeLoaders.adminShell)
const AdminUsersPage = lazy(routeLoaders.adminUsers)
const AdminReviewsPage = lazy(routeLoaders.adminReviews)
const AdminAuditPage = lazy(routeLoaders.adminAudit)
const AdminReportsPage = lazy(routeLoaders.adminReports)
// 維運（/admin/operations/*）：外殼（標題＋子導覽、ops.read 守門）＋各子頁；jobs／incidents／host 讀 DB 投影。
const OperationsLayout = lazy(routeLoaders.adminOps)
const OpsOverviewPage = lazy(routeLoaders.adminOpsOverview)
const OpsServicesPage = lazy(routeLoaders.adminOpsServices)
const OpsServiceDetailPage = lazy(routeLoaders.adminOpsService)
const OpsLogsPage = lazy(routeLoaders.adminOpsLogs)
const OpsJobsPage = lazy(routeLoaders.adminOpsJobs)
const OpsIncidentsPage = lazy(routeLoaders.adminOpsIncidents)
const OpsHostPage = lazy(routeLoaders.adminOpsHost)
const OpsDataHealthPage = lazy(routeLoaders.adminOpsDataHealth)
const OpsLlmUsagePage = lazy(routeLoaders.adminOpsLlmUsage)

function NotFound() {
  return <div style={{ padding: 20 }}>找不到頁面</div>
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
          { path: '/monitor', element: <Suspense><MonitorPage /></Suspense> },
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
          { index: true, element: <Navigate to="/admin/users" replace /> },
          { path: 'users', element: <Suspense><AdminUsersPage /></Suspense> },
          { path: 'reviews', element: <Suspense><AdminReviewsPage /></Suspense> },
          { path: 'audit', element: <Suspense><AdminAuditPage /></Suspense> },
          { path: 'reports', element: <Suspense><AdminReportsPage /></Suspense> },
          {
            path: 'operations',
            element: <Suspense><OperationsLayout /></Suspense>,
            children: [
              { index: true, element: <Navigate to="overview" replace /> },
              { path: 'overview', element: <Suspense><OpsOverviewPage /></Suspense> },
              { path: 'services', element: <Suspense><OpsServicesPage /></Suspense> },
              { path: 'services/:name', element: <Suspense><OpsServiceDetailPage /></Suspense> },
              { path: 'logs', element: <Suspense><OpsLogsPage /></Suspense> },
              { path: 'jobs', element: <Suspense><OpsJobsPage /></Suspense> },
              { path: 'incidents', element: <Suspense><OpsIncidentsPage /></Suspense> },
              { path: 'host', element: <Suspense><OpsHostPage /></Suspense> },
              { path: 'data-health', element: <Suspense><OpsDataHealthPage /></Suspense> },
              { path: 'llm-usage', element: <Suspense><OpsLlmUsagePage /></Suspense> },
            ],
          },
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
