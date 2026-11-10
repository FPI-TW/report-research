import { useState } from 'react'
import { CheckRow, FormDialog, isElevationCancelled } from '../../../components/console'
import type { AdminUser, GrantableScope } from '../useAdmin'
import { SCOPE_LABELS, messageOf } from './userText'
import c from '../../../components/console/Console.module.css'
import { t } from '@/i18n'

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
    <FormDialog open title={t('admin.users.privilegesDialog.adjustPermissionsFor', { username: user.username })} width={460} submitLabel={t('admin.users.privilegesDialog.save')} busy={busy} error={error}
      onSubmit={() => { void submit() }} onClose={onClose}>
      {/* 勾選項一列一個（直排）：直接當表單的子項，不包 c.checkGroup（那是橫向換行）。 */}
      <CheckRow checked={isSuper} onChange={setIsSuper}>{t('admin.users.privilegesDialog.superAdminCanGrantPermissions')}</CheckRow>
      {(Object.keys(SCOPE_LABELS) as GrantableScope[]).map(s => (
        <CheckRow key={s} checked={isSuper || scopes.includes(s)} disabled={isSuper} onChange={() => toggle(s)}>
          {SCOPE_LABELS[s]}
        </CheckRow>
      ))}
      <p className={c.fieldHint}>{t('admin.users.privilegesDialog.superAdminsAutomaticallyHaveEvery')}</p>
    </FormDialog>
  )
}
