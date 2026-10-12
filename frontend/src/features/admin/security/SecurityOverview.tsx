import { Card, Chip, StatTile, Status, type LampTone } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { Icon } from '../../../components/primitives/Icon'
import { fmtDateTime } from '../auditLabels'
import s from './Security.module.css'
import { ANCHOR_LABELS, LIVE_LABELS, STATE_LABELS, pct } from './securityLabels'
import { useAuditChainState, useSecurityAlerts, useTotpAdoption } from './useSecurity'

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '載入失敗，請重試'
}

const LIVE_TONE: Record<string, LampTone> = { ok: 'ok', broken: 'bad' }
const ANCHOR_TONE: Record<string, LampTone> = { ok: 'ok', tamper: 'bad', missing: 'idle' }

/**
 * 頁面最上面的告警橫幅（與 /healthz/security 同一個判斷）。觸發中才出現；「判斷不出來」與
 * 事件查詢失敗另外用 warn 橫幅講明，不讓它看起來像正常。每分鐘跟著告警狀態自動更新。
 */
export function SecurityBanners() {
  const q = useSecurityAlerts()
  if (q.isError) return <p className={c.error} role="alert">告警狀態載入失敗：{messageOf(q.error)}</p>
  if (!q.data) return null
  const d = q.data
  const unknown = d.state === 'unknown'
  return (
    <>
      {d.triggered.length > 0 && (
        <div className={c.banner} data-tone="bad" role="alert" aria-label="觸發中的告警">
          <span className={s.bannerIcon}><Icon name="alertTriangle" size={15} /></span>
          <div className={s.bannerBody}>
            <div className={s.bannerTitle}>{d.triggered.map(t => STATE_LABELS[t] ?? t).join('、')}</div>
            {d.accounts_over.length > 0 && (
              <div>
                連續失敗達門檻的帳號：{d.accounts_over.map(a => a.username ?? a.user_id.slice(0, 8)).join('、')}
                （只告警、不鎖帳號）
              </div>
            )}
          </div>
        </div>
      )}
      {(unknown || d.events_error) && (
        <div className={c.banner} data-tone="warn" role="status" aria-label="告警判斷不完整">
          <span className={s.bannerIcon}><Icon name="alertCircle" size={15} /></span>
          <div className={s.bannerBody}>
            {unknown && <div className={s.bannerTitle}>告警狀態{STATE_LABELS.unknown}</div>}
            {d.events_error && <div>登入事件查詢失敗（{d.events_error}），計數可能不完整。</div>}
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
  if (q.isPending) return <p className={c.empty}>載入中…</p>
  // 載入失敗的訊息在頁面最上面（SecurityBanners），這裡不重複。
  if (q.isError) return null
  const d = q.data
  const tone = (value: number, threshold: number) => (value >= threshold ? 'bad' as const : undefined)
  return (
    <section className={c.tiles} aria-label="告警狀態">
      <StatTile label={`${d.window_minutes} 分鐘內登入失敗`} value={d.login_failures}
        sub={`門檻 ${d.login_failure_threshold}`} tone={tone(d.login_failures, d.login_failure_threshold)} />
      <StatTile label="單一帳號最多連續失敗" value={d.max_account_failures}
        sub={`門檻 ${d.account_failure_threshold}`} tone={tone(d.max_account_failures, d.account_failure_threshold)} />
      <StatTile label={`${d.window_minutes} 分鐘內權限提升失敗`} value={d.elevate_failures}
        sub={`門檻 ${d.elevate_failure_threshold}`} tone={tone(d.elevate_failures, d.elevate_failure_threshold)} />
      {totp.data && (
        <StatTile label="兩步驟驗證（管理員）" value={`${totp.data.admins_enabled}／${totp.data.admins_total}`}
          sub={pct(totp.data.admins_enabled, totp.data.admins_total)} />
      )}
    </section>
  )
}

export function AuditChainCard() {
  const q = useAuditChainState()
  return (
    <Card title="稽核鏈" labelledBy="sec-chain-title">
      {q.isPending ? (
        <p className={c.empty}>載入中…</p>
      ) : q.isError ? (
        <div className={c.cardBody}><p className={c.error} role="alert">稽核鏈狀態載入失敗：{messageOf(q.error)}</p></div>
      ) : (
        <div className={c.cardBody}>
          <dl className={c.kv}>
            <dt>即時驗證</dt>
            <dd>
              <span className={s.kvLine}>
                <Status tone={LIVE_TONE[q.data.live.state] ?? 'warn'}>{LIVE_LABELS[q.data.live.state] ?? q.data.live.state}</Status>
                {q.data.live.total != null && <span className={s.kvExtra}>{q.data.live.total} 列</span>}
                {(q.data.live.broken_count ?? 0) > 0 && <span className={s.kvExtra}>{q.data.live.broken_count} 列對不上</span>}
              </span>
            </dd>
            <dt>驗證時間</dt>
            <dd>
              <span className={s.kvLine}>
                {fmtDateTime(q.data.live.checked_at)}
                <span className={s.kvExtra}>結果快取 {Math.round(q.data.live.cache_seconds / 60)} 分鐘</span>
              </span>
            </dd>
            <dt>最後錨定</dt>
            <dd>
              <span className={s.kvLine}>
                <Status tone={ANCHOR_TONE[q.data.anchor.state] ?? 'warn'}>{ANCHOR_LABELS[q.data.anchor.state] ?? q.data.anchor.state}</Status>
                {q.data.anchor.at && <span className={s.kvExtra}>{fmtDateTime(q.data.anchor.at)}</span>}
              </span>
            </dd>
            {q.data.anchor.message && (<><dt>錨定訊息</dt><dd>{q.data.anchor.message}</dd></>)}
            {q.data.anchor.anchors_checked != null && (
              <><dt>比對過的錨點</dt><dd>{q.data.anchor.anchors_checked} 個</dd></>
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
    <Card title="兩步驟驗證採用率" labelledBy="sec-totp-title">
      {q.isPending ? (
        <p className={c.empty}>載入中…</p>
      ) : q.isError ? (
        <div className={c.cardBody}><p className={c.error} role="alert">採用率載入失敗：{messageOf(q.error)}</p></div>
      ) : (
        <>
          <div className={c.cardBody}>
            <dl className={c.kv}>
              <dt>全體</dt>
              <dd>{`${q.data.users_enabled}／${q.data.users_total}（${pct(q.data.users_enabled, q.data.users_total)}）`}</dd>
              <dt>管理員</dt>
              <dd>{`${q.data.admins_enabled}／${q.data.admins_total}（${pct(q.data.admins_enabled, q.data.admins_total)}）`}</dd>
              {q.data.admins_without_totp.length > 0 && (
                <>
                  <dt>未開啟的管理員</dt>
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
              ? '管理員兩步驟驗證強制已開啟（ADMIN_MFA_REQUIRED）：未開啟的管理員用不了管理功能。'
              : '目前依個人設定開關，不強制；這裡只顯示採用狀況。'}
          </p>
        </>
      )}
    </Card>
  )
}
