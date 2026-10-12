import { useState } from 'react'
import { FormDialog, Field } from '../../../components/console'
import { useAdminActions, type AdminUser } from '../useAdmin'
import { messageOf, passwordProblem } from './userText'
import c from '../../../components/console/Console.module.css'
import styles from './Users.module.css'

/** 重設某個帳號的密碼。開著才掛載；後端的 400（政策不符）原樣顯示在對話框裡。 */
export function ResetPasswordDialog({ user, onClose, onDone }: {
  user: AdminUser; onClose: () => void; onDone: (msg: string) => void
}) {
  const { resetPassword } = useAdminActions()
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [error, setError] = useState<string | null>(null)

  const submit = () => {
    const problem = passwordProblem(pw) ?? (pw !== pw2 ? '兩次輸入的密碼不一致' : null)
    if (problem) { setError(problem); return }
    setError(null)
    resetPassword.mutate({ id: user.id, password: pw }, {
      onSuccess: u => { onClose(); onDone(`已重設「${u.username}」的密碼，該帳號所有裝置都已登出`) },
      onError: err => setError(messageOf(err)),
    })
  }

  return (
    <FormDialog open title={`重設「${user.username}」的密碼`} width={440} submitLabel="重設密碼"
      busy={resetPassword.isPending} error={error} onSubmit={submit} onClose={onClose}>
      <Field label="新密碼">
        <input className={c.input} type="password" value={pw} autoComplete="new-password" maxLength={256}
          onChange={e => { setPw(e.target.value); setError(null) }} />
      </Field>
      <Field label="再輸入一次">
        <input className={c.input} type="password" value={pw2} autoComplete="new-password" maxLength={256}
          onChange={e => { setPw2(e.target.value); setError(null) }} />
      </Field>
      <p className={styles.dialogHint}>重設後該帳號所有已登入的裝置都會被登出。</p>
    </FormDialog>
  )
}
