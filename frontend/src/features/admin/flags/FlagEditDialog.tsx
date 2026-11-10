import { useState } from 'react'
import { CheckRow, Field, FieldGroup, FormDialog, Segmented, isElevationCancelled } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type { FlagItem, FlagUpdateRequest } from '../../../lib/generated/adminApi'
import { useAdminUsers } from '../useAdmin'
import { buildUpdate, ROLE_LABELS, type FlagMode, type FlagRole } from './flagText'
import s from './Flags.module.css'
import { t } from '@/i18n'

const NOTE_MAX = 500
const MODE_OPTIONS: readonly { value: FlagMode; label: string }[] = [
  { value: 'on', get label() { return t('admin.flags.flagEditDialog.onForEveryone') } },
  { value: 'off', get label() { return t('admin.flags.flagEditDialog.offForEveryone') } },
  { value: 'scoped', get label() { return t('admin.flags.flagEditDialog.onForSelected') } },
]

function initialMode(flag: FlagItem): FlagMode {
  const o = flag.override
  if (!o) return flag.default ? 'on' : 'off'
  if (!o.enabled) return 'off'
  return o.allow_roles == null && o.allow_users == null ? 'on' : 'scoped'
}

function UserPicker({ selected, onChange }: { selected: string[]; onChange: (ids: string[]) => void }) {
  const users = useAdminUsers()
  if (users.isPending) return <p className={c.fieldHint}>{t('admin.flags.flagEditDialog.loadingAccounts')}</p>
  if (users.isError) return <p className={c.error} role="alert">{t('admin.flags.flagEditDialog.couldnTLoadAccounts', { message: users.error.message })}</p>
  const live = users.data.filter((u) => u.enabled || selected.includes(u.id))
  return (
    <div className={s.userList}>
      {live.map((u) => (
        <CheckRow key={u.id} checked={selected.includes(u.id)}
          onChange={(on) => onChange(on ? [...selected, u.id] : selected.filter((x) => x !== u.id))}>
          {u.username}{u.role === 'admin' ? t('admin.flags.flagEditDialog.admin') : ''}{u.enabled ? '' : t('admin.flags.flagEditDialog.disabled')}
        </CheckRow>
      ))}
    </div>
  )
}

function FlagForm({ flag, onClose, onSave }: {
  flag: FlagItem
  onClose: () => void
  onSave: (body: FlagUpdateRequest) => Promise<void>
}) {
  const [mode, setMode] = useState<FlagMode>(() => initialMode(flag))
  const [roles, setRoles] = useState<FlagRole[]>(() => [...(flag.override?.allow_roles ?? [])])
  const [users, setUsers] = useState<string[]>(() => (flag.override?.allow_users ?? []).map((u) => u.id))
  const [note, setNote] = useState(flag.override?.note ?? '')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    const body = buildUpdate(mode, roles, users, note)
    if (!body) { setError(t('admin.flags.flagEditDialog.onForSelectedNeedsAt')); return }
    setError(null)
    setBusy(true)
    try {
      await onSave(body)
    } catch (err) {
      // 在「重新驗證身分」按了取消：表單留著，不當成錯誤。
      if (!isElevationCancelled(err)) setError(err instanceof Error && err.message ? err.message : t('admin.flags.flagEditDialog.saveFailedTryAgain'))
    } finally {
      setBusy(false)
    }
  }

  const toggleRole = (r: FlagRole, on: boolean) => setRoles(on ? [...roles, r] : roles.filter((x) => x !== r))

  return (
    <FormDialog open title={t('admin.flags.flagEditDialog.adjust', { key: flag.key })} body={flag.description} submitLabel={t('admin.flags.flagEditDialog.save')} busy={busy} error={error}
      onSubmit={() => { void submit() }} onClose={onClose}>
      {/* 分段與勾選群組不用 <Field>：它是 <label>，點標題文字會誤觸群組裡的第一個控制項。
          模式那一欄只有分段這一個具名群組，所以用 bare（外層不再帶同名的 role）。 */}
      <FieldGroup label={t('admin.flags.flagEditDialog.mode')} bare>
        <div><Segmented label={t('admin.flags.flagEditDialog.mode')} options={MODE_OPTIONS} value={mode} onChange={(v) => { setMode(v); setError(null) }} /></div>
      </FieldGroup>
      {mode === 'scoped' && (
        <>
          <FieldGroup label={t('admin.flags.flagEditDialog.roles')}>
            <div className={c.checkGroup}>
              {(['admin', 'user'] as const).map((r) => (
                <CheckRow key={r} checked={roles.includes(r)} onChange={(on) => toggleRole(r, on)}>{ROLE_LABELS[r]}</CheckRow>
              ))}
            </div>
          </FieldGroup>
          <FieldGroup label={t('admin.flags.flagEditDialog.accounts')}>
            <UserPicker selected={users} onChange={setUsers} />
          </FieldGroup>
          <p className={c.fieldHint}>{t('admin.flags.flagEditDialog.onWhenAnyCheckedRole')}</p>
        </>
      )}
      <Field label={t('admin.flags.flagEditDialog.noteOptionalTheAuditLog')} counter={`${note.length}/${NOTE_MAX}`}>
        <textarea className={c.textarea} value={note} onChange={(e) => setNote(e.target.value)} maxLength={NOTE_MAX} rows={2} />
      </Field>
    </FormDialog>
  )
}

/** 調整單一旗標的覆寫（全站開啟／全站關閉／限定開啟）。寫入由呼叫端以 `useElevate` 包住。 */
export function FlagEditDialog({ flag, onClose, onSave }: {
  flag: FlagItem | null
  onClose: () => void
  onSave: (flag: FlagItem, body: FlagUpdateRequest) => Promise<void>
}) {
  if (!flag) return null
  // key：換一個旗標就重建表單，初值直接取自該旗標
  return <FlagForm key={flag.key} flag={flag} onClose={onClose} onSave={(body) => onSave(flag, body)} />
}
