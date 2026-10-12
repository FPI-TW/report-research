import { useState } from 'react'
import { Field, FormDialog, Segmented, isElevationCancelled } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type { FlagItem, FlagUpdateRequest } from '../../../lib/generated/adminApi'
import { useAdminUsers } from '../useAdmin'
import { buildUpdate, ROLE_LABELS, type FlagMode, type FlagRole } from './flagText'
import s from './Flags.module.css'

const NOTE_MAX = 500
const MODE_OPTIONS: readonly { value: FlagMode; label: string }[] = [
  { value: 'on', label: '全站開啟' },
  { value: 'off', label: '全站關閉' },
  { value: 'scoped', label: '限定開啟' },
]

function initialMode(flag: FlagItem): FlagMode {
  const o = flag.override
  if (!o) return flag.default ? 'on' : 'off'
  if (!o.enabled) return 'off'
  return o.allow_roles == null && o.allow_users == null ? 'on' : 'scoped'
}

function UserPicker({ selected, onChange }: { selected: string[]; onChange: (ids: string[]) => void }) {
  const users = useAdminUsers()
  if (users.isPending) return <p className={s.hint}>載入帳號…</p>
  if (users.isError) return <p className={c.error} role="alert">無法載入帳號清單：{users.error.message}</p>
  const live = users.data.filter((u) => u.enabled || selected.includes(u.id))
  return (
    <div className={s.userList}>
      {live.map((u) => (
        <label key={u.id} className={s.checkLabel}>
          <input type="checkbox" className={c.check} checked={selected.includes(u.id)}
            onChange={(e) => onChange(e.target.checked ? [...selected, u.id] : selected.filter((x) => x !== u.id))} />
          {u.username}{u.role === 'admin' ? '（管理員）' : ''}{u.enabled ? '' : '（已停用）'}
        </label>
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
    if (!body) { setError('限定開啟至少要勾一個角色或一位使用者'); return }
    setError(null)
    setBusy(true)
    try {
      await onSave(body)
    } catch (err) {
      // 在「重新驗證身分」按了取消：表單留著，不當成錯誤。
      if (!isElevationCancelled(err)) setError(err instanceof Error && err.message ? err.message : '儲存失敗，請重試')
    } finally {
      setBusy(false)
    }
  }

  const toggleRole = (r: FlagRole, on: boolean) => setRoles(on ? [...roles, r] : roles.filter((x) => x !== r))

  return (
    <FormDialog open title={`調整「${flag.key}」`} body={flag.description} submitLabel="儲存" busy={busy} error={error}
      onSubmit={() => { void submit() }} onClose={onClose}>
      {/* 分段與勾選群組不用 <Field>：它是 <label>，點標題文字會誤觸群組裡的第一個控制項。 */}
      <div className={c.field}>
        <span className={c.fieldLabel}>模式</span>
        <div><Segmented label="模式" options={MODE_OPTIONS} value={mode} onChange={(v) => { setMode(v); setError(null) }} /></div>
      </div>
      {mode === 'scoped' && (
        <>
          <div className={c.field} role="group" aria-label="限定角色">
            <span className={c.fieldLabel}>限定角色</span>
            <div className={s.checks}>
              {(['admin', 'user'] as const).map((r) => (
                <label key={r} className={s.checkLabel}>
                  <input type="checkbox" className={c.check} checked={roles.includes(r)}
                    onChange={(e) => toggleRole(r, e.target.checked)} />
                  {ROLE_LABELS[r]}
                </label>
              ))}
            </div>
          </div>
          <div className={c.field} role="group" aria-label="限定帳號">
            <span className={c.fieldLabel}>限定帳號</span>
            <UserPicker selected={users} onChange={setUsers} />
          </div>
          <p className={s.hint}>勾到的角色或帳號任一符合即開啟；背景工作沒有身分，一律視為關。</p>
        </>
      )}
      <Field label="註記（選填；稽核只記有沒有改，不記內容）" counter={`${note.length}/${NOTE_MAX}`}>
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
