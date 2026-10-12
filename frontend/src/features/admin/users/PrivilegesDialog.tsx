import { useState } from 'react'
import { FormDialog, isElevationCancelled } from '../../../components/console'
import type { AdminUser, GrantableScope } from '../useAdmin'
import { SCOPE_LABELS, messageOf } from './userText'
import c from '../../../components/console/Console.module.css'
import styles from './Users.module.css'

export type PrivilegesBody = { is_super: boolean; scopes: GrantableScope[] }

/**
 * 調整管理員的權限。開著才掛載、換帳號時以 key 重建，初值直接取自該帳號。
 * `onSave` 由頁面以重新驗證包住：在驗證框按取消不算錯誤，表單留著；其他失敗顯示在對話框裡。
 */
export function PrivilegesDialog({ user, onClose, onSave }: {
  user: AdminUser; onClose: () => void; onSave: (u: AdminUser, body: PrivilegesBody) => Promise<void>
}) {
  const [isSuper, setIsSuper] = useState(Boolean(user.is_super))
  const [scopes, setScopes] = useState<GrantableScope[]>(() => [...(user.scopes ?? [])])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const toggle = (s: GrantableScope) => setScopes(cur => cur.includes(s) ? cur.filter(x => x !== s) : [...cur, s])

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      await onSave(user, { is_super: isSuper, scopes })
      onClose()
    } catch (err) {
      if (!isElevationCancelled(err)) setError(messageOf(err))
      setBusy(false)
    }
  }

  return (
    <FormDialog open title={`調整「${user.username}」的權限`} width={460} submitLabel="儲存" busy={busy} error={error}
      onSubmit={() => { void submit() }} onClose={onClose}>
      <label className={styles.checkRow}>
        <input type="checkbox" className={c.check} checked={isSuper} onChange={e => setIsSuper(e.target.checked)} />
        super admin（可授予權限、管理其他 super admin）
      </label>
      {(Object.keys(SCOPE_LABELS) as GrantableScope[]).map(s => (
        <label key={s} className={styles.checkRow} data-disabled={isSuper || undefined}>
          <input type="checkbox" className={c.check} checked={isSuper || scopes.includes(s)} disabled={isSuper}
            onChange={() => toggle(s)} />
          {SCOPE_LABELS[s]}
        </label>
      ))}
      <p className={styles.dialogHint}>super admin 自動擁有全部權限。調整後對方的下一個動作就生效；需要重新驗證身分。</p>
    </FormDialog>
  )
}
