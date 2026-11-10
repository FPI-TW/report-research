import { useState } from 'react'
import { FormDialog, Field } from '../../../components/console'
import { useAdminActions, type AdminUser } from '../useAdmin'
import { messageOf, passwordProblem } from './userText'
import c from '../../../components/console/Console.module.css'
import { t } from '@/i18n'

/** 重設某個帳號的密碼。開著才掛載；後端的 400（政策不符）原樣顯示在對話框裡。 */
export function ResetPasswordDialog({ user, onClose, onDone }: {
  user: AdminUser; onClose: () => void; onDone: (msg: string) => void
}) {
  const { resetPassword } = useAdminActions()
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [error, setError] = useState<string | null>(null)

  const submit = () => {
    const problem = passwordProblem(pw) ?? (pw !== pw2 ? t('admin.users.resetPasswordDialog.thePasswordsDonTMatch') : null)
    if (problem) { setError(problem); return }
    setError(null)
    resetPassword.mutate({ id: user.id, password: pw }, {
      onSuccess: u => { onClose(); onDone(t('admin.users.resetPasswordDialog.resetThePasswordForAll', { username: u.username })) },
      onError: err => setError(messageOf(err)),
    })
  }

  return (
    <FormDialog open title={t('admin.users.resetPasswordDialog.resetPasswordFor', { username: user.username })} width={440} submitLabel={t('admin.users.resetPasswordDialog.resetPassword')}
      busy={resetPassword.isPending} error={error} onSubmit={submit} onClose={onClose}>
      <Field label={t('admin.users.resetPasswordDialog.newPassword')}>
        <input className={c.input} type="password" value={pw} autoComplete="new-password" maxLength={256}
          onChange={e => { setPw(e.target.value); setError(null) }} />
      </Field>
      <Field label={t('admin.users.resetPasswordDialog.enterAgain')}>
        <input className={c.input} type="password" value={pw2} autoComplete="new-password" maxLength={256}
          onChange={e => { setPw2(e.target.value); setError(null) }} />
      </Field>
      <p className={c.fieldHint}>{t('admin.users.resetPasswordDialog.afterResettingEverySignedIn')}</p>
    </FormDialog>
  )
}
