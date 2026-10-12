import { Link } from 'react-router'
import { Lamp, type LampTone } from '../../components/primitives/Lamp'
import { ADMIN_IA, flattenPages, visibleDomains, type AdminPageKey } from '../../components/shell/adminNav'
import { RequireAdmin } from '../../components/shell/RequireAdmin'
import { useMe } from '../../lib/useMe'
import type { OpsServiceListResponse } from '../../lib/generated/adminApi'
import type { AdminSignals, PageSignal } from './adminSignals'
import { TIER_HINTS, TIER_LABELS, TIERS, needsAttention } from './ops/opsLabels'
import { useAdminSignals, type SourceState } from './useAdminSignals'
import styles from './Overview.module.css'

const PAGE_TO = Object.fromEntries(flattenPages(ADMIN_IA).map(e => [e.page.key, e.page.to])) as Record<AdminPageKey, string>

interface Tile { key: AdminPageKey; label: string; value: string; sub: string; tone: LampTone }

/** 還沒讀到、讀不到時的磚：不講「正常」。 */
function unknownTile(key: AdminPageKey, label: string, state: SourceState): Tile {
  return { key, label, value: '—', sub: state === 'error' ? '讀不到' : '讀取中', tone: 'idle' }
}

function buildTiles(
  signals: AdminSignals, sources: Record<string, SourceState>, services: OpsServiceListResponse | undefined,
): Tile[] {
  const tiles: Tile[] = []
  const tone = (s: PageSignal | undefined): LampTone => s?.lamp ?? 'ok'

  if (sources.services !== 'off') {
    if (services) {
      const bad = services.items.filter(needsAttention)
      tiles.push({
        key: 'services', label: '服務', tone: bad.length ? 'bad' : 'ok',
        value: `${services.items.length - bad.length}／${services.items.length}`,
        sub: bad.length ? `${bad.length} 個需要處理：${bad.map(s => s.name).join('、')}` : '沒有失敗的服務',
      })
    } else if (signals.services) {
      tiles.push({ key: 'services', label: '服務', tone: 'warn', value: '讀不到', sub: signals.services.reason ?? '' })
    } else {
      tiles.push(unknownTile('services', '服務', sources.services))
    }
  }
  if (sources.pipeline !== 'off') {
    const s = signals.pipeline
    tiles.push(sources.pipeline !== 'ready' ? unknownTile('pipeline', '管線', sources.pipeline) : {
      key: 'pipeline', label: '管線', tone: tone(s),
      value: s?.lamp === 'bad' ? '有異常' : s?.lamp === 'warn' ? '注意' : '正常',
      sub: s?.reason ?? '近 24 小時沒有排程失敗',
    })
  }
  if (sources.incidents !== 'off') {
    const s = signals.incidents
    tiles.push(sources.incidents !== 'ready' ? unknownTile('incidents', '事件', sources.incidents) : {
      key: 'incidents', label: '事件', tone: tone(s),
      value: s?.count ? `${s.count} 進行中` : '0 進行中',
      sub: s?.detail ?? '沒有進行中的事件',
    })
  }
  if (sources.security !== 'off') {
    const s = signals.security
    tiles.push(sources.security !== 'ready' ? unknownTile('security', '安全', sources.security) : {
      key: 'security', label: '安全', tone: tone(s),
      value: s?.lamp ? '告警中' : '正常',
      sub: s?.reason ?? '沒有觸發中的告警',
    })
  }
  if (sources.dataHealth !== 'off') {
    const s = signals.dataHealth
    tiles.push(sources.dataHealth !== 'ready' ? unknownTile('dataHealth', '資料健康', sources.dataHealth) : {
      key: 'dataHealth', label: '資料健康', tone: tone(s),
      value: s?.lamp === 'bad' ? '異常' : s?.lamp === 'warn' ? '注意' : '正常',
      sub: s?.detail ?? (s?.lamp ? '' : '新鮮度、稽核、R2 對帳都正常'),
    })
  }
  return tiles
}

const ACTION_LABEL: Partial<Record<AdminPageKey, string>> = {
  services: '看服務', pipeline: '看管線', incidents: '看事件', host: '看主機', dataHealth: '看資料健康', security: '看安全',
}

/**
 * 總覽（/app/admin）：管理後台的首頁。狀態磚、「異常與注意」與「待處理」都來自外殼導覽同一組燈號
 * （`useAdminSignals`，同一份快取，不多送請求）；沒有對應 scope 的區塊不畫。
 * 服務矩陣、今日用量與最近操作在後續階段補上。
 */
function Overview() {
  const me = useMe()
  const scopes = me.data?.scopes ?? []
  const { signals, sources, services } = useAdminSignals({ enabled: true, scopes })
  const visible = flattenPages(visibleDomains(scopes))
  const tiles = buildTiles(signals, sources, services)
  const alerts = visible
    .filter(e => signals[e.page.key]?.lamp)
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

  return (
    <div className={styles.page}>
      <div className={styles.inner}>
        <header className={styles.header}>
          <h1 className={styles.title}>總覽</h1>
          <p className={styles.summary}>{summary}</p>
        </header>

        {tiles.length > 0 && (
          <section className={styles.tiles} aria-label="狀態">
            {tiles.map(t => (
              <Link key={t.key} to={PAGE_TO[t.key]} className={styles.tile}>
                <span className={styles.tileLabel}><Lamp tone={t.tone} />{t.label}</span>
                <span className={styles.tileValue} data-tone={t.tone}>{t.value}</span>
                <span className={styles.tileSub}>{t.sub}</span>
              </Link>
            ))}
          </section>
        )}

        <div className={styles.columns}>
          <section className={styles.card} aria-labelledby="overview-alerts">
            <div className={styles.cardHead}>
              <h2 id="overview-alerts" className={styles.cardTitle}>異常與注意</h2>
              {alerts.length > 0 && <span className={styles.badge}>{alerts.length}</span>}
            </div>
            {alerts.length === 0 ? (
              <p className={styles.empty}>沒有異常</p>
            ) : (
              <ul className={styles.alerts}>
                {alerts.map(a => (
                  <li key={a.page.key} className={styles.alert}>
                    <Lamp tone={a.signal.lamp!} size={7} className={styles.alertLamp} />
                    <div className={styles.alertBody}>
                      <div className={styles.alertTitle}>{a.signal.reason ?? a.page.label}</div>
                      {a.signal.detail && <div className={styles.alertDetail}>{a.signal.detail}</div>}
                    </div>
                    <Link to={a.page.to} className={styles.rowBtn}>{ACTION_LABEL[a.page.key] ?? `看${a.page.label}`}</Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {(reviews || drafts) && (
            <section className={styles.card} aria-labelledby="overview-queues">
              <div className={styles.cardHead}>
                <h2 id="overview-queues" className={styles.cardTitle}>待處理</h2>
              </div>
              <div className={styles.queues}>
                {reviews && (
                  <Link to={PAGE_TO.reviews} className={styles.queue}>
                    <span className={styles.queueN}>{reviews.count ?? 0}</span>
                    <span className={styles.queueLabel}>待複核</span>
                    {reviews.detail && <span className={styles.queueMeta}>{reviews.detail}</span>}
                  </Link>
                )}
                {drafts && (
                  <Link to={`${PAGE_TO.uploads}?tab=draft`} className={styles.queue}>
                    <span className={styles.queueN}>{drafts.count ?? 0}</span>
                    <span className={styles.queueLabel}>待審草稿</span>
                    <span className={styles.queueMeta}>上傳後等待發布或退回</span>
                  </Link>
                )}
              </div>
            </section>
          )}
        </div>

        {services && services.items.length > 0 && (
          <section className={styles.card} aria-labelledby="overview-services">
            <div className={styles.cardHead}>
              <h2 id="overview-services" className={styles.cardTitle}>服務</h2>
              <Link to={PAGE_TO.services} className={styles.cardLink}>所有服務</Link>
            </div>
            <div className={styles.tiers}>
              {TIERS.map(tier => {
                const inTier = services.items.filter(s => s.tier === tier)
                // 「正常」＝不需要處理：排程服務兩次執行之間的閒置算正常，和狀態磚、導覽燈號同一個判準。
                const bad = inTier.filter(needsAttention)
                return (
                  <div key={tier} className={styles.tier} aria-label={`${TIER_LABELS[tier]}服務`}>
                    <div className={styles.tierName}>{TIER_LABELS[tier]}</div>
                    <div className={styles.tierCount} data-bad={bad.length > 0 || undefined}>
                      {inTier.length - bad.length}／{inTier.length}
                    </div>
                    <div className={styles.tierHint}>正常／總數・{TIER_HINTS[tier]}</div>
                    {bad.length > 0 && (
                      <div className={styles.chips}>
                        {bad.map(s => (
                          <Link key={s.name} to={`${PAGE_TO.services}/${encodeURIComponent(s.name)}`}
                            className={styles.chipBad} title={s.description || s.target}>
                            <Lamp tone="bad" label="" />{s.name}
                          </Link>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </section>
        )}
      </div>
    </div>
  )
}

export default function AdminOverviewPage() {
  return <RequireAdmin><Overview /></RequireAdmin>
}
