import { Card, Chip, StatTile, Status, type LampTone } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { Icon } from '../../../components/primitives/Icon'
import { fmtDateTime } from '../auditLabels'
import s from './Security.module.css'
import { ANCHOR_LABELS, LIVE_LABELS, STATE_LABELS, pct } from './securityLabels'
import { useAuditChainState, useSecurityAlerts, useTotpAdoption } from './useSecurity'
import { t } from '@/i18n'

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : t('admin.security.securityOverview.couldnTLoadTryAgain')
}

const LIVE_TONE: Record<string, LampTone> = { ok: 'ok', broken: 'bad' }
const ANCHOR_TONE: Record<string, LampTone> = { ok: 'ok', tamper: 'bad', missing: 'idle' }

/**
 * 頁面最上面的告警橫幅（與 /healthz/security 同一個判斷）。觸發中才出現；「判斷不出來」與
 * 事件查詢失敗另外用 warn 橫幅講明，不讓它看起來像正常。每分鐘跟著告警狀態自動更新。
 */
export function SecurityBanners() {
  const q = useSecurityAlerts()
  if (q.isError) return <p className={c.error} role="alert">{t('admin.security.securityOverview.couldnTLoadAlertStatus', { error: messageOf(q.error) })}</p>
  if (!q.data) return null
  const d = q.data
  const unknown = d.state === 'unknown'
  return (
    <>
      {d.triggered.length > 0 && (
        <div className={c.banner} data-tone="bad" role="alert" aria-label={t('admin.security.securityOverview.alertsFiring')}>
          <span className={s.bannerIcon}><Icon name="alertTriangle" size={15} /></span>
          <div className={s.bannerBody}>
            <div className={s.bannerTitle}>{d.triggered.map(item => STATE_LABELS[item] ?? item).join(t('admin.security.securityOverview.text'))}</div>
            {d.accounts_over.length > 0 && (
              <div>
                {t('admin.security.securityOverview.accountsWithConsecutiveFailuresAt')}{d.accounts_over.map(a => a.username ?? a.user_id.slice(0, 8)).join(t('admin.security.securityOverview.text'))}
                {t('admin.security.securityOverview.alertOnlyAccountNotLocked')}
              </div>
            )}
          </div>
        </div>
      )}
      {(unknown || d.events_error) && (
        <div className={c.banner} data-tone="warn" role="status" aria-label={t('admin.security.securityOverview.alertCheckIncomplete')}>
          <span className={s.bannerIcon}><Icon name="alertCircle" size={15} /></span>
          <div className={s.bannerBody}>
            {unknown && <div className={s.bannerTitle}>{t('admin.security.securityOverview.alertStatus', { unknown: STATE_LABELS.unknown })}</div>}
            {d.events_error && <div>{t('admin.security.securityOverview.signInEventQueryFailed', { events_error: d.events_error })}</div>}
          </div>
        </div>
      )}
    </>
  )
}

/** 告警用的三個計數（值／門檻；達門檻標紅）＋管理員的兩步驟驗證採用率。 */
export function AlertTiles() {
  const q = useSecurityAlerts()
  const totp = useTotpAdoption()
  if (q.isPending) return <p className={c.empty}>{t('admin.security.securityOverview.loading')}</p>
  // 載入失敗的訊息在頁面最上面（SecurityBanners），這裡不重複。
  if (q.isError) return null
  const d = q.data
  const tone = (value: number, threshold: number) => (value >= threshold ? 'bad' as const : undefined)
  return (
    <section className={c.tiles} aria-label={t('admin.security.securityOverview.alertStatus2')}>
      <StatTile label={t('admin.security.securityOverview.signInFailuresInMin', { window_minutes: d.window_minutes })} value={d.login_failures}
        sub={t('admin.security.securityOverview.threshold', { login_failure_threshold: d.login_failure_threshold })} tone={tone(d.login_failures, d.login_failure_threshold)} />
      <StatTile label={t('admin.security.securityOverview.maxConsecutiveFailuresForOne')} value={d.max_account_failures}
        sub={t('admin.security.securityOverview.threshold2', { account_failure_threshold: d.account_failure_threshold })} tone={tone(d.max_account_failures, d.account_failure_threshold)} />
      <StatTile label={t('admin.security.securityOverview.elevationFailuresInMin', { window_minutes: d.window_minutes })} value={d.elevate_failures}
        sub={t('admin.security.securityOverview.threshold3', { elevate_failure_threshold: d.elevate_failure_threshold })} tone={tone(d.elevate_failures, d.elevate_failure_threshold)} />
      {totp.data && (
        <StatTile label={t('admin.security.securityOverview.twoStepVerificationAdmins')} value={t('admin.security.securityOverview.text2', { admins_enabled: totp.data.admins_enabled, admins_total: totp.data.admins_total })}
          sub={pct(totp.data.admins_enabled, totp.data.admins_total)} />
      )}
    </section>
  )
}

export function AuditChainCard() {
  const q = useAuditChainState()
  return (
    <Card title={t('admin.security.securityOverview.auditChain')} labelledBy="sec-chain-title">
      {q.isPending ? (
        <p className={c.empty}>{t('admin.security.securityOverview.loading')}</p>
      ) : q.isError ? (
        <div className={c.cardBody}><p className={c.error} role="alert">{t('admin.security.securityOverview.couldnTLoadAuditChain', { error: messageOf(q.error) })}</p></div>
      ) : (
        <div className={c.cardBody}>
          <dl className={c.kv}>
            <dt>{t('admin.security.securityOverview.liveVerification')}</dt>
            <dd>
              <span className={s.kvLine}>
                <Status tone={LIVE_TONE[q.data.live.state] ?? 'warn'}>{LIVE_LABELS[q.data.live.state] ?? q.data.live.state}</Status>
                {q.data.live.total != null && <span className={s.kvExtra}>{t('admin.security.securityOverview.rows', { total: q.data.live.total })}</span>}
                {(q.data.live.broken_count ?? 0) > 0 && <span className={s.kvExtra}>{q.data.live.broken_count}{' '}{t('admin.security.securityOverview.rowsDonTMatch')}</span>}
              </span>
            </dd>
            <dt>{t('admin.security.securityOverview.verifiedAt')}</dt>
            <dd>
              <span className={s.kvLine}>
                {fmtDateTime(q.data.live.checked_at)}
                <span className={s.kvExtra}>{t('admin.security.securityOverview.resultCachedMin', { round: Math.round(q.data.live.cache_seconds / 60) })}</span>
              </span>
            </dd>
            <dt>{t('admin.security.securityOverview.lastAnchored')}</dt>
            <dd>
              <span className={s.kvLine}>
                <Status tone={ANCHOR_TONE[q.data.anchor.state] ?? 'warn'}>{ANCHOR_LABELS[q.data.anchor.state] ?? q.data.anchor.state}</Status>
                {q.data.anchor.at && <span className={s.kvExtra}>{fmtDateTime(q.data.anchor.at)}</span>}
              </span>
            </dd>
            {q.data.anchor.message && (<><dt>{t('admin.security.securityOverview.anchorMessage')}</dt><dd>{q.data.anchor.message}</dd></>)}
            {q.data.anchor.anchors_checked != null && (
              <><dt>{t('admin.security.securityOverview.anchorsCompared')}</dt><dd>{t('admin.security.securityOverview.text3', { anchors_checked: q.data.anchor.anchors_checked })}</dd></>
            )}
          </dl>
        </div>
      )}
    </Card>
  )
}

export function TotpAdoptionCard() {
  const q = useTotpAdoption()
  return (
    <Card title={t('admin.security.securityOverview.twoStepVerificationAdoption')} labelledBy="sec-totp-title">
      {q.isPending ? (
        <p className={c.empty}>{t('admin.security.securityOverview.loading')}</p>
      ) : q.isError ? (
        <div className={c.cardBody}><p className={c.error} role="alert">{t('admin.security.securityOverview.couldnTLoadAdoption', { error: messageOf(q.error) })}</p></div>
      ) : (
        <>
          <div className={c.cardBody}>
            <dl className={c.kv}>
              <dt>{t('admin.security.securityOverview.everyone')}</dt>
              <dd>{t('admin.security.securityOverview.text4', { users_enabled: q.data.users_enabled, users_total: q.data.users_total, users_enabled2: pct(q.data.users_enabled, q.data.users_total) })}</dd>
              <dt>{t('admin.security.securityOverview.admins')}</dt>
              <dd>{t('admin.security.securityOverview.text5', { admins_enabled: q.data.admins_enabled, admins_total: q.data.admins_total, admins_enabled2: pct(q.data.admins_enabled, q.data.admins_total) })}</dd>
              {q.data.admins_without_totp.length > 0 && (
                <>
                  <dt>{t('admin.security.securityOverview.adminsWithoutIt')}</dt>
                  <dd>
                    <span className={s.kvLine}>
                      {q.data.admins_without_totp.map(a => (
                        <span key={a.id} className={s.kvLine}>
                          <Chip tone="bad" mono>{a.username}</Chip>
                          {a.is_super && <Chip tone="gold">super</Chip>}
                        </span>
                      ))}
                    </span>
                  </dd>
                </>
              )}
            </dl>
          </div>
          <p className={s.foot}>
            {q.data.policy_required
              ? t('admin.security.securityOverview.mandatoryAdminTwoStepVerification')
              : t('admin.security.securityOverview.currentlyEachPersonSOwn')}
          </p>
        </>
      )}
    </Card>
  )
}
