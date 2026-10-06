import { useEffect, useState, type FormEvent } from 'react'
import { ConfirmDialog } from '../../components/primitives/ConfirmDialog'
import { Modal } from '../../components/primitives/Modal'
import { RequireAdmin } from '../../components/shell/RequireAdmin'
import type { Role } from '../../lib/adminSchemas'
import { adminApi } from '../../lib/generated/adminApi'
import { useMe } from '../../lib/useMe'
import { ElevationCancelledError, useElevationGate } from '../account/useElevationGate'
import { AdminHeader } from './AdminHeader'
import { fmtDateTime, roleLabel } from './auditLabels'
import { deletionCountdown } from './deletionCountdown'
import { useAdminActions, useAdminUsers, type AdminUser, type GrantableScope } from './useAdmin'
import styles from './Admin.module.css'

/** 錯誤物件 → 給人看的訊息。後端 400／409 的 detail 由 requestJSON 放進 message，原樣顯示。 */
function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '操作失敗，請重試'
}

/** 每 30 秒更新一次的「現在」：刪除倒數只需要分鐘精度。 */
function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(t)
  }, [intervalMs])
  return now
}

const SCOPE_LABELS: Record<GrantableScope, string> = {
  'qa_content.read': '查看問答內容（qa_content.read）',
  'ops.operate': '執行維運操作（ops.operate）',
}

// 密碼政策與後端 app/services/passwords.py 相同；這裡只是提早提示，後端仍會再驗一次。
const MIN_PASSWORD = 10

function CreateUserForm({ onDone }: { onDone: (msg: string) => void }) {
  const { create } = useAdminActions()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<Role>('user')
  const [error, setError] = useState<string | null>(null)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    create.mutate({ username: username.trim(), password, role }, {
      onSuccess: user => {
        setUsername('')
        setPassword('')
        setRole('user')
        onDone(`已建立帳號「${user.username}」（${roleLabel(user.role)}）`)
      },
      onError: err => setError(messageOf(err)),
    })
  }

  return (
    <section className={styles.card} aria-labelledby="admin-create-title">
      <h2 id="admin-create-title" className={styles.ctitle}>建立帳號</h2>
      <form className={styles.form} onSubmit={submit}>
        <label className={styles.field}>帳號
          <input value={username} onChange={e => setUsername(e.target.value)} autoComplete="off" required
            minLength={2} maxLength={64} />
        </label>
        <label className={styles.field}>初始密碼
          <input type="password" value={password} onChange={e => setPassword(e.target.value)}
            autoComplete="new-password" required minLength={MIN_PASSWORD} maxLength={256} />
        </label>
        <label className={styles.field}>角色
          <select value={role} onChange={e => setRole(e.target.value as Role)}>
            <option value="user">一般使用者</option>
            <option value="admin">管理員</option>
          </select>
        </label>
        <button type="submit" className={styles.primary} disabled={create.isPending}>
          {create.isPending ? '建立中…' : '建立'}
        </button>
      </form>
      {error && <p className={styles.error} role="alert">{error}</p>}
      <p className={styles.hint}>
        帳號 2–64 個字元（文字、數字與 . _ @ -），不分大小寫；密碼至少 {MIN_PASSWORD} 個字元。
        請把初始密碼以安全的管道交給對方。
      </p>
    </section>
  )
}

function ResetPasswordDialog({ user, onClose, onDone }: {
  user: AdminUser | null; onClose: () => void; onDone: (msg: string) => void
}) {
  const { resetPassword } = useAdminActions()
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [error, setError] = useState<string | null>(null)
  const close = () => { setPw(''); setPw2(''); setError(null); onClose() }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!user) return
    if (pw !== pw2) { setError('兩次輸入的密碼不一致'); return }
    setError(null)
    resetPassword.mutate({ id: user.id, password: pw }, {
      onSuccess: u => { close(); onDone(`已重設「${u.username}」的密碼，該帳號所有裝置都已登出`) },
      onError: err => setError(messageOf(err)),
    })
  }

  return (
    <Modal open={user != null} onClose={close} title={user ? `重設「${user.username}」的密碼` : ''}>
      <form className={styles.dialogForm} onSubmit={submit}>
        <label className={styles.field}>新密碼
          <input type="password" value={pw} onChange={e => setPw(e.target.value)} autoComplete="new-password"
            required minLength={MIN_PASSWORD} maxLength={256} />
        </label>
        <label className={styles.field}>再輸入一次
          <input type="password" value={pw2} onChange={e => setPw2(e.target.value)} autoComplete="new-password"
            required minLength={MIN_PASSWORD} maxLength={256} />
        </label>
        <p className={styles.hint}>重設後該帳號所有已登入的裝置都會被登出。</p>
        {error && <p className={styles.error} role="alert">{error}</p>}
        <div className={styles.dialogActions}>
          <button type="button" className={styles.action} onClick={close}>取消</button>
          <button type="submit" className={styles.primary} disabled={resetPassword.isPending}>重設密碼</button>
        </div>
      </form>
    </Modal>
  )
}

function PrivilegesDialog({ user, onClose, onSave }: {
  user: AdminUser | null
  onClose: () => void
  onSave: (u: AdminUser, body: { is_super: boolean; scopes: GrantableScope[] }) => Promise<void>
}) {
  return (
    <Modal open={user != null} onClose={onClose} title={user ? `調整「${user.username}」的權限` : ''}>
      {/* key：換一個帳號就重建表單，初值直接取自該帳號（不在 effect 裡同步 state） */}
      {user && <PrivilegesForm key={user.id} user={user} onClose={onClose} onSave={onSave} />}
    </Modal>
  )
}

function PrivilegesForm({ user, onClose, onSave }: {
  user: AdminUser
  onClose: () => void
  onSave: (u: AdminUser, body: { is_super: boolean; scopes: GrantableScope[] }) => Promise<void>
}) {
  const [isSuper, setIsSuper] = useState(Boolean(user.is_super))
  const [scopes, setScopes] = useState<GrantableScope[]>(() => [...(user.scopes ?? [])])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const toggle = (s: GrantableScope) => setScopes(cur => cur.includes(s) ? cur.filter(x => x !== s) : [...cur, s])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await onSave(user, { is_super: isSuper, scopes })
      onClose()
    } catch (err) {
      if (!(err instanceof ElevationCancelledError)) setError(messageOf(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className={styles.dialogForm} onSubmit={submit}>
      <label className={styles.check}>
        <input type="checkbox" checked={isSuper} onChange={e => setIsSuper(e.target.checked)} />
        super admin（可授予權限、管理其他 super admin）
      </label>
      {(Object.keys(SCOPE_LABELS) as GrantableScope[]).map(s => (
        <label key={s} className={styles.check}>
          <input type="checkbox" checked={isSuper || scopes.includes(s)} disabled={isSuper}
            onChange={() => toggle(s)} />
          {SCOPE_LABELS[s]}
        </label>
      ))}
      <p className={styles.hint}>super admin 自動擁有全部權限。調整後對方的下一個動作就生效；需要重新驗證身分。</p>
      {error && <p className={styles.error} role="alert">{error}</p>}
      <div className={styles.dialogActions}>
        <button type="button" className={styles.action} onClick={onClose}>取消</button>
        <button type="submit" className={styles.primary} disabled={busy}>儲存</button>
      </div>
    </form>
  )
}

type Pending =
  | { kind: 'disable'; user: AdminUser }
  | { kind: 'demote'; user: AdminUser }
  | { kind: 'logout'; user: AdminUser }
  | { kind: 'delete'; user: AdminUser }
  | { kind: 'resetTotp'; user: AdminUser }

const CONFIRM_TEXT: Record<Pending['kind'], (u: AdminUser) => { title: string; body: string; label: string }> = {
  disable: u => ({
    title: `停用「${u.username}」？`,
    body: '停用後立即生效：該帳號所有裝置會在下一個動作時被登出，也無法再登入。可以隨時重新啟用（需重新登入）。',
    label: '停用',
  }),
  demote: u => ({
    title: `拿掉「${u.username}」的管理員權限？`,
    body: '改為一般使用者後，對方立即失去帳號管理與待複核的權限。',
    label: '改為一般使用者',
  }),
  logout: u => ({
    title: `強制登出「${u.username}」？`,
    body: '該帳號所有已登入的裝置都會被登出，需要重新輸入密碼。帳號本身不受影響。',
    label: '強制登出',
  }),
  delete: u => ({
    title: `刪除「${u.username}」？`,
    body: '帳號會立即停用並登出所有裝置，24 小時後永久刪除該帳號的問答紀錄、對話與回饋，帳號名稱與登入資料一併清除（操作紀錄保留）。24 小時內可以取消。',
    label: '排程刪除',
  }),
  resetTotp: u => ({
    title: `重設「${u.username}」的兩步驟驗證？`,
    body: '給遺失驗證器的人用：關閉後對方只用密碼就能登入，之後可自行在「帳號安全」重新開啟。',
    label: '重設兩步驟驗證',
  }),
}

function UsersTable({ onNotice }: { onNotice: (msg: string, isError?: boolean) => void }) {
  const users = useAdminUsers()
  const me = useMe()
  const now = useNow()
  const { update, forceLogout, setPrivileges, requestDeletion, cancelDeletion, resetTotp } = useAdminActions()
  const [pending, setPending] = useState<Pending | null>(null)
  const [resetFor, setResetFor] = useState<AdminUser | null>(null)
  const [privilegesFor, setPrivilegesFor] = useState<AdminUser | null>(null)
  const { guard, dialog } = useElevationGate(adminApi.elevate, Boolean(me.data?.totp_enabled))
  const isSuper = Boolean(me.data?.is_super)
  const busy = update.isPending || forceLogout.isPending || requestDeletion.isPending || cancelDeletion.isPending
    || resetTotp.isPending

  /** 需要已提升權限的動作：收到 elevation_required 時彈出驗證框、驗證後自動重試；取消不算錯誤。 */
  const elevated = async (action: () => Promise<unknown>, ok: string) => {
    try {
      await guard(action)
      onNotice(ok)
    } catch (err) {
      if (!(err instanceof ElevationCancelledError)) onNotice(messageOf(err), true)
    }
  }

  const run = (p: Pending) => {
    setPending(null)
    const onError = (err: unknown) => onNotice(messageOf(err), true)
    if (p.kind === 'delete') {
      void elevated(() => requestDeletion.mutateAsync(p.user.id),
        `已排程刪除「${p.user.username}」：帳號已停用，24 小時後執行`)
    } else if (p.kind === 'resetTotp') {
      void elevated(() => resetTotp.mutateAsync(p.user.id), `已重設「${p.user.username}」的兩步驟驗證`)
    } else if (p.kind === 'logout') {
      forceLogout.mutate(p.user.id, {
        onSuccess: r => onNotice(`已強制登出「${p.user.username}」（${r.revoked} 個 session）`),
        onError,
      })
    } else if (p.kind === 'disable') {
      update.mutate({ id: p.user.id, enabled: false }, { onSuccess: () => onNotice(`已停用「${p.user.username}」`), onError })
    } else {
      update.mutate({ id: p.user.id, role: 'user' }, {
        onSuccess: () => onNotice(`「${p.user.username}」已改為一般使用者`), onError,
      })
    }
  }
  // 提升權限與重新啟用不是破壞性的，不另外確認。
  const promote = (u: AdminUser) => update.mutate({ id: u.id, role: 'admin' }, {
    onSuccess: () => onNotice(`「${u.username}」已設為管理員`), onError: err => onNotice(messageOf(err), true),
  })
  const enable = (u: AdminUser) => update.mutate({ id: u.id, enabled: true }, {
    onSuccess: () => onNotice(`已啟用「${u.username}」`), onError: err => onNotice(messageOf(err), true),
  })
  const cancel = (u: AdminUser) => cancelDeletion.mutate(u.id, {
    onSuccess: () => onNotice(`已取消刪除「${u.username}」`), onError: err => onNotice(messageOf(err), true),
  })
  const savePrivileges = async (u: AdminUser, body: { is_super: boolean; scopes: GrantableScope[] }) => {
    await guard(() => setPrivileges.mutateAsync({ id: u.id, ...body }))
    onNotice(`已更新「${u.username}」的權限`)
  }

  const confirm = pending ? CONFIRM_TEXT[pending.kind](pending.user) : null

  return (
    <section className={styles.card} aria-labelledby="admin-users-title">
      <h2 id="admin-users-title" className={styles.ctitle}>帳號清單</h2>
      {users.isPending ? (
        <p className={styles.idle}>載入中…</p>
      ) : users.isError ? (
        <p className={styles.error} role="alert">帳號清單載入失敗：{messageOf(users.error)}</p>
      ) : users.data.length === 0 ? (
        <p className={styles.idle}>還沒有任何帳號</p>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>帳號</th><th>角色</th><th>狀態</th><th>最後登入／活動</th><th>session</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              {users.data.map(u => {
                const isSelf = me.data?.id != null && me.data.id === u.id
                const selfHint = isSelf ? '不能停用自己，也不能拿掉自己的管理員權限' : undefined
                const sessions = u.active_sessions ?? 0
                const deletingAt = u.deletion_execute_after ?? null
                return (
                  <tr key={u.id}>
                    <td>
                      {u.username}{isSelf && <span className={styles.self}>（你）</span>}
                      {u.is_super && <span className={styles.self}>super</span>}
                      {u.totp_enabled && <span className={styles.self} title="已開啟兩步驟驗證">2FA</span>}
                    </td>
                    <td>{roleLabel(u.role)}</td>
                    <td>
                      {deletingAt ? (
                        <>
                          <span className={`${styles.badge} ${styles.badgeOff}`}>排程刪除</span>
                          <div className={styles.muted} title={`預定 ${fmtDateTime(deletingAt)} 執行`}>
                            {deletionCountdown(deletingAt, now)}
                          </div>
                        </>
                      ) : (
                        <span className={`${styles.badge} ${u.enabled ? '' : styles.badgeOff}`}>{u.enabled ? '啟用' : '停用'}</span>
                      )}
                    </td>
                    {/* 兩個時間疊成一欄：管理頁卡片在一般筆電寬度只有 ~600px，分兩欄會把操作按鈕擠出去 */}
                    <td className={styles.num}>
                      <div title="最後登入">{fmtDateTime(u.last_login_at)}</div>
                      <div className={styles.muted} title="最後活動">{fmtDateTime(u.last_seen_at)}</div>
                    </td>
                    <td className={styles.num} title="有效 session 數">{sessions}</td>
                    <td className={styles.actionsCell}>
                      {deletingAt ? (
                        <div className={styles.actions}>
                          <button type="button" className={styles.action} disabled={busy}
                            onClick={() => cancel(u)}>取消刪除</button>
                        </div>
                      ) : (
                      <div className={styles.actions}>
                        {u.role === 'admin' ? (
                          <button type="button" className={styles.action} disabled={busy || isSelf} title={selfHint}
                            onClick={() => setPending({ kind: 'demote', user: u })}>改為一般使用者</button>
                        ) : (
                          <button type="button" className={styles.action} disabled={busy}
                            onClick={() => promote(u)}>設為管理員</button>
                        )}
                        {u.enabled ? (
                          <button type="button" className={`${styles.action} ${styles.danger}`} disabled={busy || isSelf}
                            title={selfHint} onClick={() => setPending({ kind: 'disable', user: u })}>停用</button>
                        ) : (
                          <button type="button" className={styles.action} disabled={busy} onClick={() => enable(u)}>啟用</button>
                        )}
                        <button type="button" className={styles.action} disabled={busy}
                          onClick={() => setResetFor(u)}>重設密碼</button>
                        <button type="button" className={styles.action} disabled={busy || sessions === 0}
                          title={sessions === 0 ? '目前沒有已登入的裝置' : undefined}
                          onClick={() => setPending({ kind: 'logout', user: u })}>強制登出</button>
                        {isSuper && u.role === 'admin' && (
                          <button type="button" className={styles.action} disabled={busy}
                            onClick={() => setPrivilegesFor(u)}>調整權限</button>
                        )}
                        {u.totp_enabled && (
                          <button type="button" className={styles.action} disabled={busy}
                            onClick={() => setPending({ kind: 'resetTotp', user: u })}>重設兩步驟驗證</button>
                        )}
                        <button type="button" className={`${styles.action} ${styles.danger}`} disabled={busy || isSelf}
                          title={isSelf ? '不能刪除自己的帳號' : undefined}
                          onClick={() => setPending({ kind: 'delete', user: u })}>刪除帳號</button>
                      </div>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className={styles.hint}>
        停用、降級、重設密碼都在對方的下一個動作生效。刪除帳號會先停用並等待 24 小時（期間可取消），
        之後永久刪除該帳號的問答紀錄；操作紀錄保留。刪除、調整權限、重設兩步驟驗證需要重新驗證身分。
      </p>
      <ConfirmDialog
        open={confirm != null}
        title={confirm?.title ?? ''}
        body={confirm?.body ?? ''}
        confirmLabel={confirm?.label ?? '確認'}
        onConfirm={() => { if (pending) run(pending) }}
        onCancel={() => setPending(null)}
      />
      <ResetPasswordDialog user={resetFor} onClose={() => setResetFor(null)} onDone={msg => onNotice(msg)} />
      <PrivilegesDialog user={privilegesFor} onClose={() => setPrivilegesFor(null)} onSave={savePrivileges} />
      {dialog}
    </section>
  )
}

function AdminUsers() {
  const [notice, setNotice] = useState<{ msg: string; isError: boolean } | null>(null)
  const onNotice = (msg: string, isError = false) => setNotice({ msg, isError })
  return (
    <div className={styles.page}>
      <div className={styles.inner}>
        <AdminHeader title="帳號管理" subtitle="建立帳號、切換角色、停用、重設密碼、強制登出、調整權限與刪除帳號；每一筆操作都會留在「操作紀錄」。" />
        {notice && (
          <p className={notice.isError ? styles.error : styles.ok} role={notice.isError ? 'alert' : 'status'}>{notice.msg}</p>
        )}
        <CreateUserForm onDone={msg => onNotice(msg)} />
        <UsersTable onNotice={onNotice} />
      </div>
    </div>
  )
}

export default function AdminUsersPage() {
  return <RequireAdmin><AdminUsers /></RequireAdmin>
}
