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
import { t } from '@/i18n'

type TabKey = 'overview' | 'events' | 'sessions' | 'risk'
const TABS: readonly { value: TabKey; label: string }[] = [
  { value: 'overview', get label() { return t('admin.security.adminSecurityPage.overview') } },
  { value: 'events', get label() { return t('admin.security.adminSecurityPage.events') } },
  { value: 'sessions', label: 'Session' },
  { value: 'risk', get label() { return t('admin.security.adminSecurityPage.highRisk') } },
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
  const tabs = TABS.filter(item => item.value !== 'sessions' || canManage)
  const [tabParam, setTabParam] = useQueryParam('tab')
  const tab = tabs.find(item => item.value === tabParam)?.value ?? 'overview'

  return (
    <ConsolePage>
      <PageHeader
        summary={t('admin.security.adminSecurityPage.signInEventsSuspiciousIps')}
        help={t('admin.security.adminSecurityPage.signInEventsSuspiciousIps2')}
      />
      <SecurityBanners />
      <Tabs label={t('admin.security.adminSecurityPage.securityTabs')} tabs={tabs} value={tab} onChange={v => setTabParam(v === 'overview' ? null : v)} />
      <div className={s.stack} role="tabpanel" aria-label={tabs.find(item => item.value === tab)?.label}>
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
      <RequireScope scope="audit.read" title={t('admin.security.adminSecurityPage.security')}>
        <AdminSecurity />
      </RequireScope>
    </RequireAdmin>
  )
}
