import { useState } from 'react'
import { meSecurityApi } from '../../lib/accountSecurityApi'
import { ApiError } from '../../lib/api'
import { startSsoElevation } from '../../lib/ssoElevation'
import { Field } from './controls'
import { FormDialog } from './Dialog'
import c from './Console.module.css'
import o from './Overlay.module.css'
import { t } from '@/i18n'

/**
 * 「重新驗證身分」對話框（密碼＋開了兩步驟驗證時的 6 位數驗證碼）。流程在 `useElevate`。
 * SSO 登入的帳號（`sso`）沒有本地密碼：改成回公司帳號重新登入，回來後再做一次原本的操作。
 */
export function ElevateDialog({ what, needTotp, sso = false, onDone, onCancel }: {
  what: string; needTotp: boolean; sso?: boolean; onDone: (elevatedUntil: string) => void; onCancel: () => void
}) {
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [askCode, setAskCode] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const showCode = needTotp || askCode

  const submit = async () => {
    if (!password) { setError(t('admin.console.elevateDialog.enterYourPassword')); return }
    if (showCode && !/^\d{6}$/.test(code)) { setError(t('admin.console.elevateDialog.theCodeIs6Digits')); return }
    setError(null)
    setBusy(true)
    try {
      const res = await meSecurityApi.elevate({ password, code: showCode ? code : undefined })
      onDone(res.elevated_until)
    } catch (err) {
      if (err instanceof ApiError && err.code === 'totp_required') {
        setAskCode(true)
        setError(t('admin.console.elevateDialog.thisAccountUsesTwoStep'))
      } else {
        setError(err instanceof Error && err.message ? err.message : t('admin.console.elevateDialog.verificationFailedTryAgain'))
      }
      setBusy(false)
    }
  }

  if (sso) {
    return (
      <FormDialog open top title={t('admin.console.elevateDialog.confirmIdentity')} width={400} submitLabel={t('admin.console.elevateDialog.reVerifyWithCompanyAccount')} busy={false}
        body={<>{t('admin.console.elevateDialog.requiresReVerificationSignIn', { what })}</>}
        onSubmit={startSsoElevation} onClose={onCancel}>{null}</FormDialog>
    )
  }

  return (
    <FormDialog open top title={t('admin.console.elevateDialog.confirmIdentity')} width={400} submitLabel={busy ? t('admin.console.elevateDialog.verifying') : t('admin.console.elevateDialog.verify')} busy={busy}
      body={<>{t('admin.console.elevateDialog.requiresReVerificationNoNeed', { what })}</>}
      error={error} onSubmit={() => { void submit() }} onClose={onCancel}>
      <Field label={t('admin.console.elevateDialog.password')}>
        <input className={c.input} type="password" value={password} autoComplete="current-password" maxLength={256}
          onChange={e => { setPassword(e.target.value); setError(null) }} />
      </Field>
      {showCode && (
        <Field label={t('admin.console.elevateDialog.verificationCode')}>
          <input className={`${c.input} ${o.codeInput}`} value={code} inputMode="numeric" autoComplete="one-time-code"
            placeholder={t('admin.console.elevateDialog.6DigitCodeFromThe')}
            onChange={e => { setCode(e.target.value.replace(/\D/g, '').slice(0, 6)); setError(null) }} />
        </Field>
      )}
    </FormDialog>
  )
}
