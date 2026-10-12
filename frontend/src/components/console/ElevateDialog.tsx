import { useState } from 'react'
import { meSecurityApi } from '../../lib/accountSecurityApi'
import { ApiError } from '../../lib/api'
import { Field } from './controls'
import { FormDialog } from './Dialog'
import c from './Console.module.css'
import o from './Overlay.module.css'

/** 「重新驗證身分」對話框（密碼＋開了兩步驟驗證時的 6 位數驗證碼）。流程在 `useElevate`。 */
export function ElevateDialog({ what, needTotp, onDone, onCancel }: {
  what: string; needTotp: boolean; onDone: (elevatedUntil: string) => void; onCancel: () => void
}) {
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [askCode, setAskCode] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const showCode = needTotp || askCode

  const submit = async () => {
    if (!password) { setError('請輸入密碼'); return }
    if (showCode && !/^\d{6}$/.test(code)) { setError('驗證碼是 6 位數字'); return }
    setError(null)
    setBusy(true)
    try {
      const res = await meSecurityApi.elevate({ password, code: showCode ? code : undefined })
      onDone(res.elevated_until)
    } catch (err) {
      if (err instanceof ApiError && err.code === 'totp_required') {
        setAskCode(true)
        setError('這個帳號開了兩步驟驗證，請輸入驗證器上的 6 位數字')
      } else {
        setError(err instanceof Error && err.message ? err.message : '驗證失敗，請重試')
      }
      setBusy(false)
    }
  }

  return (
    <FormDialog open top title="重新驗證身分" width={400} submitLabel={busy ? '驗證中…' : '驗證'} busy={busy}
      body={<>「{what}」需要重新驗證。驗證後 10 分鐘內不用再輸入。</>}
      error={error} onSubmit={() => { void submit() }} onClose={onCancel}>
      <Field label="密碼">
        <input className={c.input} type="password" value={password} autoComplete="current-password" maxLength={256}
          onChange={e => { setPassword(e.target.value); setError(null) }} />
      </Field>
      {showCode && (
        <Field label="驗證碼">
          <input className={`${c.input} ${o.codeInput}`} value={code} inputMode="numeric" autoComplete="one-time-code"
            placeholder="驗證器上的 6 位數字"
            onChange={e => { setCode(e.target.value.replace(/\D/g, '').slice(0, 6)); setError(null) }} />
        </Field>
      )}
    </FormDialog>
  )
}
