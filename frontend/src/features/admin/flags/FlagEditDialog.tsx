import { useState, type FormEvent } from 'react'
import { Modal } from '../../../components/primitives/Modal'
import type { FlagItem, FlagUpdateRequest } from '../../../lib/generated/adminApi'
import { useAdminUsers } from '../useAdmin'
import adminStyles from '../Admin.module.css'
import { buildUpdate, ROLE_LABELS, type FlagMode, type FlagRole } from './flagText'
import styles from './Flags.module.css'

type Mode = FlagMode
type Role = FlagRole

function initialMode(flag: FlagItem): Mode {
  const o = flag.override
  if (!o) return flag.default ? 'on' : 'off'
  if (!o.enabled) return 'off'
  return o.allow_roles == null && o.allow_users == null ? 'on' : 'scoped'
}

function UserPicker({ selected, onChange }: { selected: string[]; onChange: (ids: string[]) => void }) {
  const users = useAdminUsers()
  if (users.isPending) return <p className={adminStyles.idle}>載入帳號…</p>
  if (users.isError) return <p className={adminStyles.error} role="alert">無法載入帳號清單：{users.error.message}</p>
  const live = users.data.filter((u) => u.enabled || selected.includes(u.id))
  return (
    <div className={styles.userList} role="group" aria-label="指定使用者">
      {live.map((u) => (
        <label key={u.id} className={adminStyles.check}>
          <input type="checkbox" checked={selected.includes(u.id)}
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
  const [mode, setMode] = useState<Mode>(() => initialMode(flag))
  const [roles, setRoles] = useState<Role[]>(() => [...(flag.override?.allow_roles ?? [])])
  const [users, setUsers] = useState<string[]>(() => (flag.override?.allow_users ?? []).map((u) => u.id))
  const [note, setNote] = useState(flag.override?.note ?? '')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const body = buildUpdate(mode, roles, users, note)
    if (!body) { setError('限定開啟至少要勾一個角色或一位使用者'); return }
    setError(null)
    setBusy(true)
    try {
      await onSave(body)
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : '儲存失敗，請重試')
    } finally {
      setBusy(false)
    }
  }

  const toggleRole = (r: Role, on: boolean) => setRoles(on ? [...roles, r] : roles.filter((x) => x !== r))

  return (
    <form className={adminStyles.dialogForm} onSubmit={(e) => void submit(e)}>
      <p className={adminStyles.hint}>{flag.description}</p>
      <fieldset className={styles.fieldset}>
        <legend>政策</legend>
        <label className={adminStyles.check}>
          <input type="radio" name="mode" checked={mode === 'on'} onChange={() => setMode('on')} />全站開啟
        </label>
        <label className={adminStyles.check}>
          <input type="radio" name="mode" checked={mode === 'off'} onChange={() => setMode('off')} />全站關閉（降級）
        </label>
        <label className={adminStyles.check}>
          <input type="radio" name="mode" checked={mode === 'scoped'} onChange={() => setMode('scoped')} />只對部分使用者開啟
        </label>
      </fieldset>
      {mode === 'scoped' && (
        <>
          <fieldset className={styles.fieldset}>
            <legend>角色</legend>
            {(['admin', 'user'] as const).map((r) => (
              <label key={r} className={adminStyles.check}>
                <input type="checkbox" checked={roles.includes(r)} onChange={(e) => toggleRole(r, e.target.checked)} />
                {ROLE_LABELS[r]}
              </label>
            ))}
          </fieldset>
          <fieldset className={styles.fieldset}>
            <legend>使用者</legend>
            <UserPicker selected={users} onChange={setUsers} />
          </fieldset>
          <p className={adminStyles.hint}>勾到的角色或使用者任一符合即開啟；背景工作沒有身分，一律視為關。</p>
        </>
      )}
      <label className={adminStyles.field}>註記（選填，最多 500 字；稽核只記有沒有改，不記內容）
        <textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} rows={2} />
      </label>
      {error && <p className={adminStyles.error} role="alert">{error}</p>}
      <div className={adminStyles.dialogActions}>
        <button type="button" className={adminStyles.action} onClick={onClose}>取消</button>
        <button type="submit" className={adminStyles.primary} disabled={busy}>儲存</button>
      </div>
    </form>
  )
}

/** 調整單一旗標的覆寫（全站開／全站關／限定）。寫入由呼叫端以 useElevationGate 包住。 */
export function FlagEditDialog({ flag, onClose, onSave }: {
  flag: FlagItem | null
  onClose: () => void
  onSave: (flag: FlagItem, body: FlagUpdateRequest) => Promise<void>
}) {
  return (
    <Modal open={flag != null} onClose={onClose} title={flag ? `調整「${flag.key}」` : ''}>
      {/* key：換一個旗標就重建表單，初值直接取自該旗標 */}
      {flag && <FlagForm key={flag.key} flag={flag} onClose={onClose} onSave={(body) => onSave(flag, body)} />}
    </Modal>
  )
}
