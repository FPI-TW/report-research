import { useState, type FormEvent } from 'react'
import { ConfirmDialog } from '../../components/primitives/ConfirmDialog'
import { Modal } from '../../components/primitives/Modal'
import { RequireAdmin } from '../../components/shell/RequireAdmin'
import type { AdminUser, Role } from '../../lib/adminSchemas'
import { useMe } from '../../lib/useMe'
import { AdminHeader } from './AdminHeader'
import { fmtDateTime, roleLabel } from './auditLabels'
import { useAdminActions, useAdminUsers } from './useAdmin'
import styles from './Admin.module.css'

/** 錯誤物件 → 給人看的訊息。後端 400／409 的 detail 由 requestJSON 放進 message，原樣顯示。 */
function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '操作失敗，請重試'
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

type Pending =
  | { kind: 'disable'; user: AdminUser }
  | { kind: 'demote'; user: AdminUser }
  | { kind: 'logout'; user: AdminUser }

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
}

function UsersTable({ onNotice }: { onNotice: (msg: string, isError?: boolean) => void }) {
  const users = useAdminUsers()
  const me = useMe()
  const { update, forceLogout } = useAdminActions()
  const [pending, setPending] = useState<Pending | null>(null)
  const [resetFor, setResetFor] = useState<AdminUser | null>(null)
  const busy = update.isPending || forceLogout.isPending

  const run = (p: Pending) => {
    setPending(null)
    const onError = (err: unknown) => onNotice(messageOf(err), true)
    if (p.kind === 'logout') {
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
                return (
                  <tr key={u.id}>
                    <td>{u.username}{isSelf && <span className={styles.self}>（你）</span>}</td>
                    <td>{roleLabel(u.role)}</td>
                    <td>
                      <span className={`${styles.badge} ${u.enabled ? '' : styles.badgeOff}`}>{u.enabled ? '啟用' : '停用'}</span>
                    </td>
                    {/* 兩個時間疊成一欄：管理頁卡片在一般筆電寬度只有 ~600px，分兩欄會把操作按鈕擠出去 */}
                    <td className={styles.num}>
                      <div title="最後登入">{fmtDateTime(u.last_login_at)}</div>
                      <div className={styles.muted} title="最後活動">{fmtDateTime(u.last_seen_at)}</div>
                    </td>
                    <td className={styles.num} title="有效 session 數">{u.active_sessions}</td>
                    <td className={styles.actionsCell}>
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
                        <button type="button" className={styles.action} disabled={busy || u.active_sessions === 0}
                          title={u.active_sessions === 0 ? '目前沒有已登入的裝置' : undefined}
                          onClick={() => setPending({ kind: 'logout', user: u })}>強制登出</button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className={styles.hint}>帳號只停用、不刪除：歷史問答與操作紀錄都指向它。停用、降級、重設密碼都在對方的下一個動作生效。</p>
      <ConfirmDialog
        open={confirm != null}
        title={confirm?.title ?? ''}
        body={confirm?.body ?? ''}
        confirmLabel={confirm?.label ?? '確認'}
        onConfirm={() => { if (pending) run(pending) }}
        onCancel={() => setPending(null)}
      />
      <ResetPasswordDialog user={resetFor} onClose={() => setResetFor(null)} onDone={msg => onNotice(msg)} />
    </section>
  )
}

function AdminUsers() {
  const [notice, setNotice] = useState<{ msg: string; isError: boolean } | null>(null)
  const onNotice = (msg: string, isError = false) => setNotice({ msg, isError })
  return (
    <div className={styles.page}>
      <div className={styles.inner}>
        <AdminHeader title="帳號管理" subtitle="建立帳號、切換角色、停用、重設密碼與強制登出；每一筆操作都會留在「操作紀錄」。" />
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
