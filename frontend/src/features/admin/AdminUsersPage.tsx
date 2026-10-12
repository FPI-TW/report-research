import { useEffect, useState } from 'react'
import {
  Button, ConfirmDialog, ConsolePage, PageHeader, SearchInput, Segmented, isElevationCancelled, toast, useElevate,
  useQueryParam, type SegOption,
} from '../../components/console'
import { Icon } from '../../components/primitives/Icon'
import { RequireAdmin } from '../../components/shell/RequireAdmin'
import { adminCsvUrls } from '../../lib/generated/adminApi'
import { useMe } from '../../lib/useMe'
import { BulkResultPanel } from './BulkParts'
import { ExportCsvButton } from './ExportCsvButton'
import { useAdminActions, useAdminUsers, type AdminUser, type BulkUserAction } from './useAdmin'
import { namesPreview, useSelection, type BulkOutcome } from './useSelection'
import { CreateUserDialog } from './users/CreateUserDialog'
import { PrivilegesDialog, type PrivilegesBody } from './users/PrivilegesDialog'
import { ResetPasswordDialog } from './users/ResetPasswordDialog'
import { UsersTable } from './users/UsersTable'
import { BULK_ACTIONS, BULK_TEXT, CONFIRM_TEXT, messageOf, type ConfirmKind } from './users/userText'
import c from '../../components/console/Console.module.css'
import styles from './users/Users.module.css'

/** 每 30 秒更新一次的「現在」：刪除倒數只需要分鐘精度。 */
function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(t)
  }, [intervalMs])
  return now
}

type UserFilter = 'all' | 'admin' | 'user' | 'off'

const FILTERS: Record<UserFilter, { label: string; match: (u: AdminUser) => boolean }> = {
  all: { label: '全部', match: () => true },
  admin: { label: '管理員', match: u => u.role === 'admin' },
  user: { label: '一般使用者', match: u => u.role === 'user' },
  off: { label: '停用', match: u => !u.enabled },
}
const FILTER_KEYS = Object.keys(FILTERS) as UserFilter[]
const isFilter = (v: string | null): v is UserFilter => v != null && v in FILTERS

const HELP = '建立帳號、切換角色、停用、重設密碼、強制登出、調整權限與刪除帳號；每一筆操作都會留在「操作紀錄」。'
  + '勾選多個帳號可一次停用、啟用或強制登出（需要重新驗證身分；被規則擋下的帳號會逐筆列出原因）。'
  + '刪除帳號會先停用並等待 24 小時（期間可取消），之後永久刪除該帳號的問答紀錄；操作紀錄保留。'
  + '刪除、調整權限、重設兩步驟驗證需要重新驗證身分。'

function AdminUsers() {
  const users = useAdminUsers()
  const me = useMe()
  const now = useNow()
  const { update, forceLogout, setPrivileges, requestDeletion, cancelDeletion, resetTotp, bulk } = useAdminActions()
  const { elevated, dialog } = useElevate()
  const sel = useSelection()
  const [q, setQ] = useState('')
  // 篩選放在 URL（`?filter=off`），連結可以分享；不認得的值當成「全部」。
  const [filterParam, setFilterParam] = useQueryParam('filter')
  const filter: UserFilter = isFilter(filterParam) ? filterParam : 'all'
  const [creating, setCreating] = useState(false)
  const [pending, setPending] = useState<{ kind: ConfirmKind; user: AdminUser } | null>(null)
  const [bulkFor, setBulkFor] = useState<{ action: BulkUserAction; users: AdminUser[] } | null>(null)
  const [outcome, setOutcome] = useState<BulkOutcome | null>(null)
  const [resetFor, setResetFor] = useState<AdminUser | null>(null)
  const [privilegesFor, setPrivilegesFor] = useState<AdminUser | null>(null)
  const busy = update.isPending || forceLogout.isPending || requestDeletion.isPending || cancelDeletion.isPending
    || resetTotp.isPending || bulk.isPending

  const list = users.data ?? []
  const myId = me.data?.id ?? null
  const needle = q.trim().toLowerCase()
  const visible = list.filter(u => FILTERS[filter].match(u) && (!needle || u.username.toLowerCase().includes(needle)))
  const filterOptions: SegOption<UserFilter>[] = FILTER_KEYS.map(k => ({
    value: k, label: FILTERS[k].label, count: users.data ? list.filter(FILTERS[k].match).length : null,
  }))

  // 批次：自己那列不給勾（不能停用自己、批次強制登出也不含自己；後端同樣擋）。
  // 清單一次載完、篩選只在前端做，所以選取跨篩選保留；確認框會列出要動到的帳號。
  const chosen = list.filter(u => u.id !== myId && sel.selected.has(u.id))
  const bulkTargets: Record<BulkUserAction, AdminUser[]> = {
    disable: chosen.filter(u => u.enabled),
    enable: chosen.filter(u => !u.enabled && !u.deletion_execute_after),
    logout: chosen.filter(u => (u.active_sessions ?? 0) > 0),
  }

  const fail = (err: unknown) => toast(messageOf(err), { tone: 'bad' })

  /** 批次要重新驗證；在驗證框按取消不算錯誤，選取留著。 */
  const runBulk = async (action: BulkUserAction, targets: AdminUser[]) => {
    const title = `批次${BULK_TEXT[action].verb}`
    const names = new Map(targets.map(u => [u.id, u.username]))
    try {
      const res = await elevated(title, () => bulk.mutateAsync({ action, user_ids: targets.map(u => u.id) }))
      const skipped = res.results.filter(r => r.status === 'skipped').map(r => ({
        name: names.get(r.user_id) ?? r.user_id, detail: r.detail ?? r.code ?? '略過',
      }))
      sel.clear()
      // 一句結果用 toast；有略過或未變更的（要花時間讀原因）才留下逐筆結果的面板。
      setOutcome(skipped.length > 0 || res.unchanged > 0 ? { title, ok: res.ok, unchanged: res.unchanged, skipped } : null)
      toast(`${title}：成功 ${res.ok} 筆、略過 ${skipped.length} 筆`)
    } catch (err) {
      if (!isElevationCancelled(err)) toast(`${title}失敗：${messageOf(err)}`, { tone: 'bad' })
    }
  }

  /** 需要重新驗證的單筆動作；在驗證框按取消不算錯誤。 */
  const runElevated = async (what: string, action: () => Promise<unknown>, ok: string) => {
    try {
      await elevated(what, action)
      toast(ok)
    } catch (err) {
      if (!isElevationCancelled(err)) fail(err)
    }
  }

  const run = ({ kind, user: u }: { kind: ConfirmKind; user: AdminUser }) => {
    setPending(null)
    if (kind === 'delete') {
      void runElevated('刪除帳號', () => requestDeletion.mutateAsync(u.id), `已排程刪除「${u.username}」：帳號已停用，24 小時後執行`)
    } else if (kind === 'resetTotp') {
      void runElevated('重設兩步驟驗證', () => resetTotp.mutateAsync(u.id), `已重設「${u.username}」的兩步驟驗證`)
    } else if (kind === 'logout') {
      forceLogout.mutate(u.id, {
        onSuccess: r => toast(`已強制登出「${u.username}」（${r.revoked} 個 session）`), onError: fail,
      })
    } else if (kind === 'disable') {
      update.mutate({ id: u.id, enabled: false }, { onSuccess: () => toast(`已停用「${u.username}」`), onError: fail })
    } else {
      update.mutate({ id: u.id, role: 'user' }, { onSuccess: () => toast(`「${u.username}」已改為一般使用者`), onError: fail })
    }
  }
  // 提升權限、重新啟用、取消刪除不是破壞性的，不另外確認。
  const promote = (u: AdminUser) => update.mutate({ id: u.id, role: 'admin' }, {
    onSuccess: () => toast(`「${u.username}」已設為管理員`), onError: fail,
  })
  const enable = (u: AdminUser) => update.mutate({ id: u.id, enabled: true }, {
    onSuccess: () => toast(`已啟用「${u.username}」`), onError: fail,
  })
  const cancel = (u: AdminUser) => cancelDeletion.mutate(u.id, {
    onSuccess: () => toast(`已取消刪除「${u.username}」`), onError: fail,
  })
  const savePrivileges = async (u: AdminUser, body: PrivilegesBody) => {
    await elevated('調整權限', () => setPrivileges.mutateAsync({ id: u.id, ...body }))
    toast(`已更新「${u.username}」的權限`)
  }

  const confirm = pending ? CONFIRM_TEXT[pending.kind](pending.user) : null
  const enabledCount = list.filter(u => u.enabled).length
  const sessionCount = list.reduce((n, u) => n + (u.active_sessions ?? 0), 0)

  return (
    <ConsolePage>
      <PageHeader summary="建立帳號、調整角色與權限、停用或刪除。" help={HELP} actions={
        <>
          <ExportCsvButton href={adminCsvUrls.exportUsers()} what="帳號清單" />
          <Button variant="primary" onClick={() => setCreating(true)}><Icon name="plus" size={14} />建立帳號</Button>
        </>
      } />
      <div className={c.toolbar}>
        <SearchInput value={q} onChange={setQ} placeholder="篩選帳號" />
        <Segmented options={filterOptions} value={filter} label="帳號篩選"
          onChange={v => setFilterParam(v === 'all' ? null : v)} />
        <div className={c.toolbarSpacer} />
        {chosen.length > 0 && (
          <div className={c.bulk} role="toolbar" aria-label="批次操作">
            <span className={styles.bulkCount}>已選 {chosen.length} 個</span>
            {BULK_ACTIONS.map(action => {
              const targets = bulkTargets[action]
              return (
                <Button key={action} size="sm" variant={action === 'disable' ? 'dangerText' : 'default'}
                  disabled={busy || targets.length === 0}
                  title={targets.length === 0 ? `選取的帳號裡沒有可以${BULK_TEXT[action].verb}的` : undefined}
                  onClick={() => setBulkFor({ action, users: targets })}>
                  {BULK_TEXT[action].verb}（{targets.length}）
                </Button>
              )
            })}
            <button type="button" className={c.iconBtn} title="清除選取" aria-label="清除選取" onClick={sel.clear}>
              <Icon name="x" size={14} />
            </button>
          </div>
        )}
      </div>
      <BulkResultPanel outcome={outcome} onClose={() => setOutcome(null)} />
      <section className={c.card} aria-label="帳號清單">
        {users.isPending ? (
          <p className={c.empty}>載入中…</p>
        ) : users.isError ? (
          <div className={styles.loadError}><p className={c.error} role="alert">帳號清單載入失敗：{messageOf(users.error)}</p></div>
        ) : list.length === 0 ? (
          <p className={c.empty}>還沒有任何帳號</p>
        ) : visible.length === 0 ? (
          <p className={c.empty}>沒有符合的帳號</p>
        ) : (
          <UsersTable users={visible} myId={myId} canGrant={Boolean(me.data?.is_super)} busy={busy} now={now}
            selected={sel.selected} onToggle={sel.toggle} onToggleAll={sel.setAll}
            ask={(kind, user) => setPending({ kind, user })} promote={promote} enable={enable} cancelDeletion={cancel}
            resetPassword={setResetFor} privileges={setPrivilegesFor} />
        )}
        {users.data && (
          <div className={c.cardFoot}>
            <span className={c.mono}>{visible.length} 個帳號・{enabledCount} 個啟用・{sessionCount} 個有效 session</span>
            <span>停用、降級、重設密碼在對方下一個動作生效</span>
          </div>
        )}
      </section>
      <ConfirmDialog
        open={confirm != null}
        title={confirm?.title ?? ''}
        body={confirm?.body ?? ''}
        confirmLabel={confirm?.label ?? '確認'}
        onConfirm={() => { if (pending) run(pending) }}
        onCancel={() => setPending(null)}
      />
      <ConfirmDialog
        open={bulkFor != null}
        tone={bulkFor?.action === 'enable' ? 'safe' : 'danger'}
        title={bulkFor ? `${BULK_TEXT[bulkFor.action].verb} ${bulkFor.users.length} 個帳號？` : ''}
        body={bulkFor
          ? `${namesPreview(bulkFor.users.map(u => u.username))}。${BULK_TEXT[bulkFor.action].body}每個帳號各留一筆操作紀錄。`
          : ''}
        confirmLabel={bulkFor ? `${BULK_TEXT[bulkFor.action].verb} ${bulkFor.users.length} 個` : '確認'}
        onConfirm={() => {
          if (!bulkFor) return
          setBulkFor(null)
          void runBulk(bulkFor.action, bulkFor.users)
        }}
        onCancel={() => setBulkFor(null)}
      />
      {creating && <CreateUserDialog onClose={() => setCreating(false)} onDone={toast} />}
      {resetFor && <ResetPasswordDialog key={resetFor.id} user={resetFor} onClose={() => setResetFor(null)} onDone={toast} />}
      {privilegesFor && (
        <PrivilegesDialog key={privilegesFor.id} user={privilegesFor} onClose={() => setPrivilegesFor(null)} onSave={savePrivileges} />
      )}
      {dialog}
    </ConsolePage>
  )
}

export default function AdminUsersPage() {
  return <RequireAdmin><AdminUsers /></RequireAdmin>
}
