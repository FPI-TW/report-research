import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { renderSVG } from 'uqr'
import { Modal } from '../../components/primitives/Modal'
import { meSecurityApi, type TotpSetup } from '../../lib/accountSecurityApi'
import { isSsoSession } from '../../lib/ssoElevation'
import { useMe } from '../../lib/useMe'
import { ElevationCancelledError, useElevationGate } from './useElevationGate'
import styles from './Account.module.css'
import { t } from '@/i18n'

const TOTP_KEY = ['me', 'totp'] as const

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : t('account.securityDialog.actionFailedTryAgain')
}

/** otpauth URI 的 QR code。固定白底黑碼（深色模式也一樣），驗證器 App 才掃得到。 */
function TotpQr({ uri }: { uri: string }) {
  const svg = useMemo(() => renderSVG(uri, { border: 4, ecc: 'L' }), [uri])
  return (
    <div className={styles.qr} role="img" aria-label={t('account.securityDialog.qrCodeForTwoStep')} data-testid="totp-qr"
      dangerouslySetInnerHTML={{ __html: svg }} />
  )
}

/**
 * 帳號安全的對話框殼：給還沒有設定彈窗可用的地方（管理後台的 MFA 強制頁）。
 * 一般介面改從設定彈窗的「安全」分頁進入（`SecurityPanel`），兩邊是同一份內容。
 */
export function SecurityDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [elevating, setElevating] = useState(false)
  return (
    <Modal open={open} onClose={() => { if (!elevating) onClose() }} title={t('account.securityDialog.accountSecurity')} className={styles.modal}>
      <SecurityPanel active={open} onElevatingChange={setElevating} />
    </Modal>
  )
}

/**
 * 帳號安全設定（設定彈窗 →「安全」）：每位使用者自己開關兩步驟驗證（TOTP）。
 *
 * 開啟：產生 secret → 顯示 QR code 給驗證器 App 掃描（在本機以 `uqr` 產生 SVG，otpauth URI 不送到任何外部服務；
 * 掃不了時可展開手動輸入金鑰）→ 輸入一次正確的驗證碼才真的啟用。關閉要先重新驗證（密碼＋驗證碼）。
 * 規則都在後端（`/api/me/totp*`），這裡只是流程。
 *
 * 管理員 TOTP 強制政策開啟時（`/api/me` 的 `mfa_policy_locked`），管理員看不到「關閉」按鈕、改顯示一行說明；
 * 擋人的是後端（403 `mfa_policy_locked`），這裡只是不讓人按一個注定失敗的按鈕。
 */
export function SecurityPanel({ active, onElevatingChange }: {
  /** 面板看得見時才查狀態。 */
  active: boolean
  /** 重新驗證的對話框開著時，外層彈窗不能被 Esc 或點遮罩關掉（Esc 會同時送到兩層）。 */
  onElevatingChange?: (elevating: boolean) => void
}) {
  const client = useQueryClient()
  const me = useMe()
  const status = useQuery({ queryKey: TOTP_KEY, queryFn: meSecurityApi.totpStatus, enabled: active, retry: false })
  const [setup, setSetup] = useState<TotpSetup | null>(null)
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const { guard, dialog, open: elevating } = useElevationGate(meSecurityApi.elevate, true,
    isSsoSession(me.data?.auth_method))
  useEffect(() => { onElevatingChange?.(elevating) }, [elevating, onElevatingChange])

  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: TOTP_KEY }),
      client.invalidateQueries({ queryKey: ['me'] }),
    ])
  }

  const run = async (fn: () => Promise<void>) => {
    setError(null)
    setNotice(null)
    setBusy(true)
    try {
      await fn()
    } catch (err) {
      if (!(err instanceof ElevationCancelledError)) setError(messageOf(err))
    } finally {
      setBusy(false)
    }
  }

  const begin = () => run(async () => {
    setSetup(await meSecurityApi.totpSetup())
    setCode('')
  })
  const confirm = (e: FormEvent) => {
    e.preventDefault()
    void run(async () => {
      await meSecurityApi.totpConfirm(code.trim())
      setSetup(null)
      setCode('')
      setNotice(t('account.securityDialog.twoStepVerificationIsOn'))
      await refresh()
    })
  }
  const disable = () => run(async () => {
    await guard(() => meSecurityApi.totpDisable())
    setNotice(t('account.securityDialog.twoStepVerificationIsOff'))
    await refresh()
  })

  const enabled = status.data?.enabled ?? me.data?.totp_enabled ?? false
  const policyLocked = me.data?.mfa_policy_locked === true

  return (
    <>
        <div className={styles.form}>
          <p className={styles.status}>
            {t('account.securityDialog.twoStepVerification')}
            <span className={`${styles.badge} ${enabled ? '' : styles.badgeOff}`}>{enabled ? t('account.securityDialog.on') : t('account.securityDialog.off')}</span>
          </p>
          {!setup && (
            <p className={styles.hint}>
              {t('account.securityDialog.whenOnSigningInAnd')}
            </p>
          )}
          {status.isError && <p className={styles.error} role="alert">{t('account.securityDialog.couldnTLoadStatus', { error: messageOf(status.error) })}</p>}
          {notice && <p className={styles.ok} role="status">{notice}</p>}
          {setup ? (
            <form className={styles.form} onSubmit={confirm} aria-label={t('account.securityDialog.confirmTwoStepVerification')}>
              <div className={styles.setup}>
                <TotpQr uri={setup.otpauth_uri} />
                <div className={styles.steps}>
                  <p className={styles.step}><span className={styles.stepNo}>1</span>{t('account.securityDialog.scanTheQrCodeWith')}</p>
                  <details className={styles.manual}>
                    <summary>{t('account.securityDialog.canTScanEnterThe')}</summary>
                    <p className={styles.secret} data-testid="totp-secret">{setup.secret}</p>
                  </details>
                  <a className={styles.openApp} href={setup.otpauth_uri}>{t('account.securityDialog.openTheAuthenticatorOnThis')}</a>
                  <label className={styles.field}>
                    <span className={styles.step}><span className={styles.stepNo}>2</span>{t('account.securityDialog.enterThe6DigitCode')}</span>
                    <input value={code} onChange={e => setCode(e.target.value)} inputMode="numeric"
                      autoComplete="one-time-code" required maxLength={7} pattern="[0-9 ]*" aria-label={t('account.securityDialog.verificationCode')} />
                  </label>
                </div>
              </div>
              <p className={styles.hint}>{t('account.securityDialog.theQrCodeAndKey')}</p>
              {error && <p className={styles.error} role="alert">{error}</p>}
              <div className={styles.actions}>
                <button type="button" className={styles.secondary} onClick={() => { setSetup(null); setError(null) }}>
                  {t('account.securityDialog.cancel')}
                </button>
                <button type="submit" className={styles.primary} disabled={busy}>{t('account.securityDialog.confirmAndTurnOn')}</button>
              </div>
            </form>
          ) : (
            <>
              {error && <p className={styles.error} role="alert">{error}</p>}
              <div className={styles.actions}>
                {enabled && policyLocked ? (
                  <p className={styles.hint} data-testid="totp-policy-locked">
                    {t('account.securityDialog.administratorAccountsCanTTurn', { v: ' ' })}<code>create_admin.py --reset-totp</code>{' '}{t('account.securityDialog.toResetIt')}
                  </p>
                ) : enabled ? (
                  <button type="button" className={`${styles.secondary} ${styles.danger}`} disabled={busy}
                    onClick={() => void disable()}>{t('account.securityDialog.turnOffTwoStepVerification')}</button>
                ) : (
                  <button type="button" className={styles.primary} disabled={busy || status.isPending}
                    onClick={() => void begin()}>{t('account.securityDialog.turnOnTwoStepVerification')}</button>
                )}
              </div>
            </>
          )}
        </div>
      {dialog}
    </>
  )
}
