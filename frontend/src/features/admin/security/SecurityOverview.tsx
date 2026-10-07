import { fmtDateTime } from '../auditLabels'
import adminStyles from '../Admin.module.css'
import styles from './Security.module.css'
import { ANCHOR_LABELS, LIVE_LABELS, STATE_LABELS, pct } from './securityLabels'
import { useAuditChainState, useSecurityAlerts, useTotpAdoption } from './useSecurity'

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '載入失敗，請重試'
}

function pillClass(kind: 'ok' | 'warn' | 'bad' | 'idle'): string {
  const map = { ok: styles.pOk, warn: styles.pWarn, bad: styles.pBad, idle: styles.pIdle }
  return `${styles.pill} ${map[kind]}`
}

function Stat({ label, value, threshold }: { label: string; value: number; threshold: number }) {
  const over = value >= threshold
  return (
    <div className={`${styles.stat} ${over ? styles.statOver : ''}`}>
      <dt>{label}</dt>
      <dd>{value}<span className={styles.statSub}>／門檻 {threshold}</span></dd>
    </div>
  )
}

/** 此刻的告警判斷（與 /healthz/security 同一個判斷；P5 只送狀態型通知，明細在這裡看）。 */
export function SecurityAlerts() {
  const q = useSecurityAlerts()
  return (
    <section className={adminStyles.card} aria-labelledby="sec-alerts-title">
      <div className={adminStyles.cardHead}>
        <h2 id="sec-alerts-title" className={adminStyles.ctitle}>告警狀態</h2>
        {q.data && (
          <span className={pillClass(q.data.state === 'ok' ? 'ok' : q.data.state === 'unknown' ? 'idle' : 'bad')}>
            {STATE_LABELS[q.data.state] ?? q.data.state}
          </span>
        )}
      </div>
      {q.isPending ? (
        <p className={adminStyles.idle}>載入中…</p>
      ) : q.isError ? (
        <p className={adminStyles.error} role="alert">告警狀態載入失敗：{messageOf(q.error)}</p>
      ) : (
        <>
          <dl className={styles.stats}>
            <Stat label={`${q.data.window_minutes} 分鐘內登入失敗`} value={q.data.login_failures}
              threshold={q.data.login_failure_threshold} />
            <Stat label="單一帳號最多連續失敗" value={q.data.max_account_failures}
              threshold={q.data.account_failure_threshold} />
            <Stat label={`${q.data.window_minutes} 分鐘內權限提升失敗`} value={q.data.elevate_failures}
              threshold={q.data.elevate_failure_threshold} />
          </dl>
          {q.data.triggered.length > 0 && (
            <ul className={styles.alertList} aria-label="觸發中的告警">
              {q.data.triggered.map(t => <li key={t}>{STATE_LABELS[t] ?? t}</li>)}
            </ul>
          )}
          {q.data.accounts_over.length > 0 && (
            <p className={adminStyles.hint}>
              連續失敗達門檻的帳號：{q.data.accounts_over.map(a => a.username ?? a.user_id.slice(0, 8)).join('、')}
              （只告警、不鎖帳號）
            </p>
          )}
          {q.data.events_error && (
            <p className={adminStyles.hint}>登入事件查詢失敗（{q.data.events_error}），計數可能不完整。</p>
          )}
          <p className={adminStyles.hint}>
            觸發時由既有事件管線（P5）送 Slack 開場、升級與恢復通知；不會為每一筆登入事件發通知，也不會自動封鎖。
          </p>
        </>
      )}
    </section>
  )
}

export function AuditChainCard() {
  const q = useAuditChainState()
  return (
    <section className={adminStyles.card} aria-labelledby="sec-chain-title">
      <h2 id="sec-chain-title" className={adminStyles.ctitle}>稽核鏈</h2>
      {q.isPending ? (
        <p className={adminStyles.idle}>載入中…</p>
      ) : q.isError ? (
        <p className={adminStyles.error} role="alert">稽核鏈狀態載入失敗：{messageOf(q.error)}</p>
      ) : (
        <dl className={styles.kv}>
          <dt>即時驗證</dt>
          <dd>
            <span className={pillClass(q.data.live.state === 'ok' ? 'ok' : q.data.live.state === 'broken' ? 'bad' : 'warn')}>
              {LIVE_LABELS[q.data.live.state] ?? q.data.live.state}
            </span>
            {q.data.live.total != null && ` ${q.data.live.total} 列`}
            {(q.data.live.broken_count ?? 0) > 0 && `，${q.data.live.broken_count} 列對不上`}
          </dd>
          <dt>驗證時間</dt>
          <dd>{fmtDateTime(q.data.live.checked_at)}（結果快取 {Math.round(q.data.live.cache_seconds / 60)} 分鐘）</dd>
          <dt>最後錨定</dt>
          <dd>
            <span className={pillClass(q.data.anchor.state === 'ok' ? 'ok'
              : q.data.anchor.state === 'tamper' ? 'bad' : q.data.anchor.state === 'missing' ? 'idle' : 'warn')}>
              {ANCHOR_LABELS[q.data.anchor.state] ?? q.data.anchor.state}
            </span>
            {q.data.anchor.at && ` ${fmtDateTime(q.data.anchor.at)}`}
          </dd>
          {q.data.anchor.message && (<><dt>錨定訊息</dt><dd>{q.data.anchor.message}</dd></>)}
          {q.data.anchor.anchors_checked != null && (
            <><dt>比對過的錨點</dt><dd>{q.data.anchor.anchors_checked} 個</dd></>
          )}
        </dl>
      )}
    </section>
  )
}

export function TotpAdoptionCard() {
  const q = useTotpAdoption()
  return (
    <section className={adminStyles.card} aria-labelledby="sec-totp-title">
      <h2 id="sec-totp-title" className={adminStyles.ctitle}>兩步驟驗證採用率</h2>
      {q.isPending ? (
        <p className={adminStyles.idle}>載入中…</p>
      ) : q.isError ? (
        <p className={adminStyles.error} role="alert">採用率載入失敗：{messageOf(q.error)}</p>
      ) : (
        <>
          <dl className={styles.kv}>
            <dt>全體</dt>
            <dd>{`${q.data.users_enabled}／${q.data.users_total}（${pct(q.data.users_enabled, q.data.users_total)}）`}</dd>
            <dt>管理員</dt>
            <dd>{`${q.data.admins_enabled}／${q.data.admins_total}（${pct(q.data.admins_enabled, q.data.admins_total)}）`}</dd>
            {q.data.admins_without_totp.length > 0 && (
              <>
                <dt>未開啟的管理員</dt>
                <dd>
                  {q.data.admins_without_totp.map((a, i) => (
                    <span key={a.id}>
                      {i > 0 && '、'}
                      <span className={styles.redName}>{a.username}</span>{a.is_super && '（super）'}
                    </span>
                  ))}
                </dd>
              </>
            )}
          </dl>
          <p className={adminStyles.hint}>
            {q.data.policy_required
              ? '管理員兩步驟驗證強制已開啟（ADMIN_MFA_REQUIRED）：未開啟的管理員用不了管理功能。'
              : '目前依個人設定開關，不強制；這裡只顯示採用狀況。'}
          </p>
        </>
      )}
    </section>
  )
}
