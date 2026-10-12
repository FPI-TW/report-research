import { useState } from 'react'
import { Link } from 'react-router'
import { Button, Card, ConsolePage, Lamp, PageHeader, toast } from '../../components/console'
import c from '../../components/console/Console.module.css'
import { Icon } from '../../components/primitives/Icon'
import { ADMIN_IA, flattenPages, visibleDomains, type AdminPageKey } from '../../components/shell/adminNav'
import { RequireAdmin } from '../../components/shell/RequireAdmin'
import { useMe } from '../../lib/useMe'
import { useOverviewDependencies, useOverviewRefresh } from './overview/overviewData'
import { QueuesCard } from './overview/QueuesCard'
import { RecentAudit } from './overview/RecentAudit'
import { ServiceMatrix } from './overview/ServiceMatrix'
import { buildTiles, externalTile } from './overview/tiles'
import { UsageCard } from './overview/UsageCard'
import { useAdminSignals } from './useAdminSignals'
import styles from './Overview.module.css'

const PAGE_TO = Object.fromEntries(flattenPages(ADMIN_IA).map(e => [e.page.key, e.page.to])) as Record<AdminPageKey, string>

const ACTION_LABEL: Partial<Record<AdminPageKey, string>> = {
  services: '看服務', dependencies: '看依賴圖', pipeline: '看管線', incidents: '看事件', host: '看主機', dataHealth: '看資料健康', security: '看安全',
}

const pad = (n: number) => String(n).padStart(2, '0')
/** 頁首的日期 kicker：2026-10-08（四）。 */
function dateKicker(now: Date): string {
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}（${'日一二三四五六'[now.getDay()]}）`
}
function clock(ms: number): string {
  const d = new Date(ms)
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/**
 * 總覽（/app/admin）：管理後台的首頁。
 *
 * 狀態磚、「異常與注意」與「待處理」的待複核、待審草稿都來自外殼導覽同一組燈號（`useAdminSignals`，
 * 同一份快取，不多送請求）；燈號判準只在 `adminSignals.ts`，這裡不另外寫。其餘區塊（外部依賴、上傳失敗、
 * 未開 2FA、服務矩陣、今日用量、最近操作）各自查、各自處理載入與失敗，一個來源壞掉不影響其他區塊。
 * 沒有對應 scope 的區塊不畫、也不送請求。
 */
function Overview() {
  const me = useMe()
  const scopes = me.data?.scopes ?? []
  const has = (scope: string) => scopes.includes(scope)
  const { signals, sources, services } = useAdminSignals({ enabled: true, scopes })
  const deps = useOverviewDependencies(has('ops.read'))
  const { updatedAt, refresh } = useOverviewRefresh()
  const [refreshing, setRefreshing] = useState(false)

  const visible = flattenPages(visibleDomains(scopes))
  const tiles = buildTiles(signals, sources, services)
  if (has('ops.read')) tiles.push(externalTile(deps))
  // 主機的燈號就是「進行中的事件裡有主機資源那一筆」（adminSignals.ts）：事件那一列已經列出來時，
  // 主機那一列是同一件事，不再列一次（燈號與狀態磚照舊，只有這張清單去重）。
  const hostIsListedAsIncident = Boolean(signals.incidents?.lamp) && visible.some(e => e.page.key === 'incidents')
  const alerts = visible
    .filter(e => signals[e.page.key]?.lamp && !(e.page.key === 'host' && hostIsListedAsIncident))
    .map(e => ({ ...e, signal: signals[e.page.key]! }))
    .sort((a, b) => (a.signal.lamp === 'bad' ? 0 : 1) - (b.signal.lamp === 'bad' ? 0 : 1))
  const nBad = alerts.filter(a => a.signal.lamp === 'bad').length
  const nWarn = alerts.length - nBad
  const reviews = sources.reviews === 'ready' ? signals.reviews : undefined
  const drafts = sources.uploads === 'ready' ? signals.uploads : undefined
  const summary = [
    `${nBad} 項異常、${nWarn} 項注意`,
    reviews && `${reviews.count ?? 0} 筆待複核`,
    drafts && `${drafts.count ?? 0} 份待審草稿`,
  ].filter(Boolean).join('・')

  const onRefresh = async () => {
    setRefreshing(true)
    try {
      await refresh()
      toast('已更新')
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <ConsolePage>
      <PageHeader
        kicker={dateKicker(new Date())}
        summary={summary}
        actions={
          <>
            <span className={styles.updated}>
              更新於 <span className={styles.updatedAt}>{updatedAt ? clock(updatedAt) : '—'}</span>
            </span>
            <Button onClick={onRefresh} disabled={refreshing} aria-busy={refreshing || undefined}>
              <Icon name="refresh" size={14} />重新整理
            </Button>
          </>
        }
      />

      {tiles.length > 0 && (
        <section className={styles.tiles} aria-label="狀態">
          {tiles.map(t => (
            <Link key={t.key} to={PAGE_TO[t.key]} className={`${c.tile} ${styles.tile}`}>
              <span className={c.tileLabel}><Lamp tone={t.tone} size={7} />{t.label}</span>
              <span className={styles.tileValue} data-tone={t.tone}>{t.value}</span>
              <span className={c.tileSub} title={t.sub}>{t.sub}</span>
            </Link>
          ))}
        </section>
      )}

      <div className={styles.columns}>
        <div className={styles.stack}>
          <Card title="異常與注意" labelledBy="overview-alerts"
            hint={alerts.length > 0 && <span className={styles.badge}>{alerts.length}</span>}>
            {alerts.length === 0 ? (
              <p className={c.empty}>沒有異常</p>
            ) : (
              <ul className={styles.alerts}>
                {alerts.map(a => (
                  <li key={a.page.key} className={styles.alert}>
                    <Lamp tone={a.signal.lamp!} size={7} className={styles.alertLamp} />
                    <div className={styles.alertBody}>
                      <div className={styles.alertTitle}>{a.signal.reason ?? a.page.label}</div>
                      {a.signal.detail && <div className={styles.alertDetail}>{a.signal.detail}</div>}
                    </div>
                    <Link to={a.page.to} className={`${c.btn} ${c.btnSm} ${styles.alertBtn}`}>
                      {ACTION_LABEL[a.page.key] ?? `看${a.page.label}`}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <QueuesCard scopes={scopes} signals={signals} sources={sources}
            to={{ reviews: PAGE_TO.reviews, uploads: PAGE_TO.uploads, security: PAGE_TO.security }} />
        </div>
        {has('ops.read') && (
          <ServiceMatrix services={services} pending={sources.services === 'pending'} servicesTo={PAGE_TO.services} deps={deps} />
        )}
      </div>

      {(has('analytics.read') || has('audit.read')) && (
        <div className={styles.columns}>
          {has('analytics.read') && <UsageCard analyticsTo={PAGE_TO.analytics} />}
          {has('audit.read') && <RecentAudit auditTo={PAGE_TO.audit} />}
        </div>
      )}
    </ConsolePage>
  )
}

export default function AdminOverviewPage() {
  return <RequireAdmin><Overview /></RequireAdmin>
}
