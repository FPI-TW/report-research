import { ConsolePage, PageHeader, Tabs, useQueryParam } from '../../../components/console'
import { RequireAdmin } from '../../../components/shell/RequireAdmin'
import { useHasScope } from '../../../lib/useMe'
import { RequireScope } from '../RequireScope'
import s from './Security.module.css'
import { AuthEventsCard, SuspiciousIpsCard } from './SecurityEvents'
import { AlertTiles, AuditChainCard, SecurityBanners, TotpAdoptionCard } from './SecurityOverview'
import { SessionsCard } from './SecuritySessions'
import { HighRiskCard } from './SecurityTimeline'
import { useIpFilter } from './useIpFilter'

type TabKey = 'overview' | 'events' | 'sessions' | 'risk'
const TABS: readonly { value: TabKey; label: string }[] = [
  { value: 'overview', label: '概況' },
  { value: 'events', label: '事件' },
  { value: 'sessions', label: 'Session' },
  { value: 'risk', label: '高風險' },
]

/** 事件分頁：可疑 IP 的「看事件」把 IP 帶進下面登入事件的篩選（`?ip=`）。 */
function EventsPanel() {
  const ip = useIpFilter()
  return (
    <>
      <SuspiciousIpsCard onPick={ip.pick} />
      <AuthEventsCard ip={ip} />
    </>
  )
}

function AdminSecurity() {
  // session 另要 accounts.manage：沒有就不畫那個分頁（顯示層；擋人的是後端）。
  const canManage = useHasScope('accounts.manage')
  const tabs = TABS.filter(t => t.value !== 'sessions' || canManage)
  const [tabParam, setTabParam] = useQueryParam('tab')
  const tab = tabs.find(t => t.value === tabParam)?.value ?? 'overview'

  return (
    <ConsolePage>
      <PageHeader
        summary="登入事件、可疑 IP、session、高風險操作與稽核鏈；只告警，不鎖帳號、不封 IP。"
        help="登入事件、可疑 IP、有效 session 與單一 session 撤銷、高風險操作時間線、稽核鏈與錨定狀態、兩步驟驗證採用率。登入事件不存帳號名稱；本站不鎖帳號、不封 IP。觸發時由既有事件管線（P5）送 Slack 開場、升級與恢復通知；不會為每一筆登入事件發通知，也不會自動封鎖。"
      />
      <SecurityBanners />
      <Tabs label="安全的分頁" tabs={tabs} value={tab} onChange={v => setTabParam(v === 'overview' ? null : v)} />
      <div className={s.stack} role="tabpanel" aria-label={tabs.find(t => t.value === tab)?.label}>
        {tab === 'overview' && (
          <>
            <AlertTiles />
            <div className={s.grid}>
              <AuditChainCard />
              <TotpAdoptionCard />
            </div>
          </>
        )}
        {tab === 'events' && <EventsPanel />}
        {tab === 'sessions' && <SessionsCard />}
        {tab === 'risk' && <HighRiskCard />}
      </div>
    </ConsolePage>
  )
}

/** 安全（/app/admin/security；Admin v2 Security lane）。後端 /api/admin/security/*（audit.read；session 另要 accounts.manage）。 */
export default function AdminSecurityPage() {
  return (
    <RequireAdmin>
      <RequireScope scope="audit.read" title="安全">
        <AdminSecurity />
      </RequireScope>
    </RequireAdmin>
  )
}
