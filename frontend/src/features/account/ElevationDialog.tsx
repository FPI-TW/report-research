import { useState, type FormEvent } from 'react'
import { Modal } from '../../components/primitives/Modal'
import { ApiError } from '../../lib/api'
import { startSsoElevation } from '../../lib/ssoElevation'
import type { ElevateFn } from './useElevationGate'
import styles from './Account.module.css'
import { t } from '@/i18n'

/**
 * 重新驗證身分的對話框（密碼＋開了 TOTP 時的驗證碼）。流程控制在 `useElevationGate`。
 * SSO 登入的帳號（`sso`）改成回公司帳號重新登入。
 */
export function ElevationDialog({ open, needTotp, sso = false, elevate, onDone, onCancel }: {
  open: boolean
  needTotp: boolean
  sso?: boolean
  elevate: ElevateFn
  onDone: () => void
  onCancel: () => void
}) {
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [askCode, setAskCode] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const showCode = needTotp || askCode

  const reset = () => { setPassword(''); setCode(''); setError(null); setAskCode(false) }
  const cancel = () => { reset(); onCancel() }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await elevate({ password, code: showCode ? code.trim() : undefined })
      reset()
      onDone()
    } catch (err) {
      if (err instanceof ApiError && err.code === 'totp_required') {
        setAskCode(true)
        setError(t('account.elevationDialog.thisAccountUsesTwoStep'))
      } else {
        setError(err instanceof Error && err.message ? err.message : t('account.elevationDialog.verificationFailedTryAgain'))
      }
    } finally {
      setBusy(false)
    }
  }

  if (sso) {
    return (
      <Modal open={open} onClose={cancel} title={t('account.elevationDialog.confirmIdentity')} className={styles.compactModal}>
        <div className={styles.form}>
          <p className={styles.hint}>{t('account.elevationDialog.thisActionRequiresSigningIn')}</p>
          <div className={styles.actions}>
            <button type="button" className={styles.secondary} onClick={cancel}>{t('account.elevationDialog.cancel')}</button>
            <button type="button" className={styles.primary} onClick={startSsoElevation}>{t('account.elevationDialog.reVerifyWithCompanyAccount')}</button>
          </div>
        </div>
      </Modal>
    )
  }

  return (
    <Modal open={open} onClose={cancel} title={t('account.elevationDialog.confirmIdentity')} className={styles.compactModal}>
      <form className={styles.form} onSubmit={submit}>
        <p className={styles.hint}>{t('account.elevationDialog.thisActionRequiresConfirmingIdentity')}</p>
        <label className={styles.field}>{t('account.elevationDialog.password')}
          <input type="password" value={password} onChange={e => setPassword(e.target.value)}
            autoComplete="current-password" required maxLength={256} autoFocus />
        </label>
        {showCode && (
          <label className={styles.field}>{t('account.elevationDialog.verificationCode')}
            <input value={code} onChange={e => setCode(e.target.value)} inputMode="numeric"
              autoComplete="one-time-code" required maxLength={7} pattern="[0-9 ]*" />
          </label>
        )}
        {error && <p className={styles.error} role="alert">{error}</p>}
        <div className={styles.actions}>
          <button type="button" className={styles.secondary} onClick={cancel}>{t('account.elevationDialog.cancel')}</button>
          <button type="submit" className={styles.primary} disabled={busy}>{busy ? t('account.elevationDialog.verifying') : t('account.elevationDialog.verify')}</button>
        </div>
      </form>
    </Modal>
  )
}
