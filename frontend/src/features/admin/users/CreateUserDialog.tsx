import { useState } from 'react'
import { FormDialog, Field, FieldGroup, Segmented, type SegOption } from '../../../components/console'
import type { Role } from '../../../lib/adminSchemas'
import { roleLabel } from '../auditLabels'
import { useAdminActions } from '../useAdmin'
import { MIN_PASSWORD, messageOf, passwordProblem, usernameProblem } from './userText'
import c from '../../../components/console/Console.module.css'
import { t } from '@/i18n'

const ROLE_OPTIONS: readonly SegOption<Role>[] = [
  { value: 'user', get label() { return t('admin.users.createUserDialog.regularUser') } },
  { value: 'admin', get label() { return t('admin.users.createUserDialog.admin') } },
]

/**
 * 建立帳號。開著才掛載（關掉就卸載），所以每次打開都是空白表單。
 * 格式檢查只是提早提示，規則與文案同後端；後端的 400／409 原樣顯示在對話框裡。
 */
export function CreateUserDialog({ onClose, onDone }: { onClose: () => void; onDone: (msg: string) => void }) {
  const { create } = useAdminActions()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<Role>('user')
  const [error, setError] = useState<string | null>(null)

  const submit = () => {
    const name = username.trim()
    const problem = usernameProblem(name) ?? passwordProblem(password)
    if (problem) { setError(problem); return }
    setError(null)
    create.mutate({ username: name, password, role }, {
      onSuccess: user => { onClose(); onDone(t('admin.users.createUserDialog.createdAccount', { username: user.username, role: roleLabel(user.role) })) },
      onError: err => setError(messageOf(err)),
    })
  }

  return (
    <FormDialog open title={t('admin.users.createUserDialog.createAccount')} width={440} submitLabel={create.isPending ? t('admin.users.createUserDialog.creating') : t('admin.users.createUserDialog.create')}
      busy={create.isPending} error={error} onSubmit={submit} onClose={onClose}>
      <Field label={t('admin.users.createUserDialog.username')}>
        <input className={`${c.input} ${c.inputMono}`} value={username} autoComplete="off" spellCheck={false} maxLength={64}
          onChange={e => { setUsername(e.target.value); setError(null) }} />
      </Field>
      <Field label={t('admin.users.createUserDialog.initialPassword')}>
        <input className={c.input} type="password" value={password} autoComplete="new-password" maxLength={256}
          onChange={e => { setPassword(e.target.value); setError(null) }} />
      </Field>
      {/* 分段是一組按鈕，不包在 <label> 裡（點標籤會誤觸第一顆）；外面那層 div 讓分段維持內容寬、不被拉滿。 */}
      <FieldGroup label={t('admin.users.createUserDialog.role')} bare>
        <div><Segmented options={ROLE_OPTIONS} value={role} onChange={setRole} label={t('admin.users.createUserDialog.role')} /></div>
      </FieldGroup>
      <p className={c.fieldHint}>
        {t('admin.users.createUserDialog.usernamesAre264Characters', { MIN_PASSWORD })}
      </p>
    </FormDialog>
  )
}
