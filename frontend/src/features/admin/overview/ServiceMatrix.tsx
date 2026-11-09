import { Link } from 'react-router'
import { Card, Lamp } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type { OpsDependencyGraph, OpsServiceListResponse } from '../../../lib/generated/adminApi'
import { SUMMARY_LABELS, TIER_HINTS, TIER_LABELS, TIERS, needsAttention } from '../ops/opsLabels'
import { healthLabel, nodeTone, serviceHref, serviceTone } from '../ops/services/health'
import { externalNodes } from './tiles'
import styles from '../Overview.module.css'
import { t } from '@/i18n'

/**
 * 「服務」矩陣：依 tier 分組，每個服務一個小晶片（等寬字＋燈號），點了開服務頁的詳情抽屜。
 * 需要處理的服務用紅框——判準是 `needsAttention`，和狀態磚、導覽燈號同一個；排程服務兩次執行之間的閒置算正常。
 * 下方是外部依賴（虛線框），狀態是後端依探針算好的。
 */
export function ServiceMatrix({ services, pending, servicesTo, deps }: {
  services: OpsServiceListResponse | undefined
  pending: boolean
  servicesTo: string
  deps: { data: OpsDependencyGraph | undefined; isError: boolean }
}) {
  // 服務清單讀不到時不畫這張卡：狀態磚與「異常與注意」已經說明原因，這裡沒有東西可列。
  if (!services && !pending) return null
  const items = services?.items ?? []
  const bad = items.filter(needsAttention).length
  const externals = deps.data ? externalNodes(deps.data) : null

  return (
    <Card title={t('admin.overview.serviceMatrix.services')} labelledBy="overview-services"
      right={services && <Link to={servicesTo} className={styles.cardStat}>{t('admin.overview.serviceMatrix.ok', { v: items.length - bad, length: items.length })}</Link>}>
      {!services ? <p className={c.empty}>{t('admin.overview.serviceMatrix.loading')}</p> : (
        <>
          {TIERS.map(tier => {
            const inTier = items.filter(s => s.tier === tier)
            const tierBad = inTier.filter(needsAttention).length
            return (
              <div key={tier} className={styles.tier} role="group" aria-label={t('admin.overview.serviceMatrix.services2', { TIER_LABELS: TIER_LABELS[tier] })}>
                <div className={styles.tierHead} title={TIER_HINTS[tier]}>
                  {TIER_LABELS[tier]}
                  <span className={styles.tierCount} data-bad={tierBad > 0 || undefined}>{t('admin.overview.serviceMatrix.text', { v: inTier.length - tierBad, length: inTier.length })}</span>
                </div>
                <div className={styles.chips}>
                  {inTier.map(s => (
                    <Link key={s.name} to={serviceHref(s.name)} className={styles.svc}
                      data-bad={needsAttention(s) || undefined}
                      aria-label={t('admin.overview.serviceMatrix.text2', { name: s.name, SUMMARY_LABELS: SUMMARY_LABELS[s.summary] })}
                      title={`${s.name}${t('common.separator')}${SUMMARY_LABELS[s.summary]}${s.description ? `${t('common.separator')}${s.description}` : ''}`}>
                      <Lamp tone={serviceTone(s)} label="" size={5} />{s.name}
                    </Link>
                  ))}
                </div>
              </div>
            )
          })}
          {(externals == null || externals.length > 0) && (
            <div className={styles.tier} role="group" aria-label={t('admin.overview.serviceMatrix.externalDependencies')}>
              <div className={styles.tierHead}>{t('admin.overview.serviceMatrix.externalDependencies')}</div>
              {externals == null ? (
                <p className={styles.tierNote}>{deps.isError ? t('admin.overview.serviceMatrix.unavailable') : t('admin.overview.serviceMatrix.loading2')}</p>
              ) : (
                <div className={styles.chips}>
                  {externals.map(n => (
                    <span key={n.name} className={styles.ext} data-health={n.health}
                      title={[n.description, n.health_reason].filter(Boolean).join(t('common.separator'))}>
                      <Lamp tone={nodeTone(n)} label={healthLabel(n)} size={5} />{n.name}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </Card>
  )
}
