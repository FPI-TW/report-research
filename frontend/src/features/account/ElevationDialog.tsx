import { useState, type FormEvent } from 'react'
import { Modal } from '../../components/primitives/Modal'
import { ApiError } from '../../lib/api'
import type { ElevateFn } from './useElevationGate'
import styles from './Account.module.css'

/** 重新驗證身分的對話框（密碼＋開了 TOTP 時的驗證碼）。流程控制在 `useElevationGate`。 */
export function ElevationDialog({ open, needTotp, elevate, onDone, onCancel }: {
  open: boolean
  needTotp: boolean
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
        setError('這個帳號開了兩步驟驗證，請輸入驗證器顯示的驗證碼')
      } else {
        setError(err instanceof Error && err.message ? err.message : '驗證失敗，請重試')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open={open} onClose={cancel} title="重新驗證身分">
      <form className={styles.form} onSubmit={submit}>
        <p className={styles.hint}>這項操作需要確認是你本人。驗證後 10 分鐘內，這個瀏覽器不必再輸入。</p>
        <label className={styles.field}>密碼
          <input type="password" value={password} onChange={e => setPassword(e.target.value)}
            autoComplete="current-password" required maxLength={256} autoFocus />
        </label>
        {showCode && (
          <label className={styles.field}>驗證碼
            <input value={code} onChange={e => setCode(e.target.value)} inputMode="numeric"
              autoComplete="one-time-code" required maxLength={7} pattern="[0-9 ]*" />
          </label>
        )}
        {error && <p className={styles.error} role="alert">{error}</p>}
        <div className={styles.actions}>
          <button type="button" className={styles.secondary} onClick={cancel}>取消</button>
          <button type="submit" className={styles.primary} disabled={busy}>{busy ? '驗證中…' : '驗證'}</button>
        </div>
      </form>
    </Modal>
  )
}
