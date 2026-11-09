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
import { t } from '@/i18n'

/** 每 30 秒更新一次的「現在」：刪除倒數只需要分鐘精度。 */
function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const item = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(item)
  }, [intervalMs])
  return now
}

type UserFilter = 'all' | 'admin' | 'user' | 'off'

const FILTERS: Record<UserFilter, { label: string; match: (u: AdminUser) => boolean }> = {
  all: { get label() { return t('admin.adminUsersPage.all') }, match: () => true },
  admin: { get label() { return t('admin.adminUsersPage.admins') }, match: u => u.role === 'admin' },
  user: { get label() { return t('admin.adminUsersPage.regularUsers') }, match: u => u.role === 'user' },
  off: { get label() { return t('admin.adminUsersPage.disabled') }, match: u => !u.enabled },
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
    const title = t('admin.adminUsersPage.batch', { verb: BULK_TEXT[action].verb })
    const names = new Map(targets.map(u => [u.id, u.username]))
    try {
      const res = await elevated(title, () => bulk.mutateAsync({ action, user_ids: targets.map(u => u.id) }))
      const skipped = res.results.filter(r => r.status === 'skipped').map(r => ({
        name: names.get(r.user_id) ?? r.user_id, detail: r.detail ?? r.code ?? t('admin.adminUsersPage.skipped'),
      }))
      sel.clear()
      // 一句結果用 toast；有略過或未變更的（要花時間讀原因）才留下逐筆結果的面板。
      setOutcome(skipped.length > 0 || res.unchanged > 0 ? { title, ok: res.ok, unchanged: res.unchanged, skipped } : null)
      toast(t('admin.adminUsersPage.succeededSkipped', { title, ok: res.ok, length: skipped.length }))
    } catch (err) {
      if (!isElevationCancelled(err)) toast(t('admin.adminUsersPage.failed', { title, err: messageOf(err) }), { tone: 'bad' })
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
      void runElevated(t('admin.adminUsersPage.deleteAccount'), () => requestDeletion.mutateAsync(u.id), t('admin.adminUsersPage.scheduledDeletionOfTheAccount', { username: u.username }))
    } else if (kind === 'resetTotp') {
      void runElevated(t('admin.adminUsersPage.resetTwoStepVerification'), () => resetTotp.mutateAsync(u.id), t('admin.adminUsersPage.resetTwoStepVerificationFor', { username: u.username }))
    } else if (kind === 'logout') {
      forceLogout.mutate(u.id, {
        onSuccess: r => toast(t('admin.adminUsersPage.signedOutSessions', { username: u.username, revoked: r.revoked })), onError: fail,
      })
    } else if (kind === 'disable') {
      update.mutate({ id: u.id, enabled: false }, { onSuccess: () => toast(t('admin.adminUsersPage.disabled2', { username: u.username })), onError: fail })
    } else {
      update.mutate({ id: u.id, role: 'user' }, { onSuccess: () => toast(t('admin.adminUsersPage.isNowARegularUser', { username: u.username })), onError: fail })
    }
  }
  // 提升權限、重新啟用、取消刪除不是破壞性的，不另外確認。
  const promote = (u: AdminUser) => update.mutate({ id: u.id, role: 'admin' }, {
    onSuccess: () => toast(t('admin.adminUsersPage.isNowAnAdmin', { username: u.username })), onError: fail,
  })
  const enable = (u: AdminUser) => update.mutate({ id: u.id, enabled: true }, {
    onSuccess: () => toast(t('admin.adminUsersPage.enabled', { username: u.username })), onError: fail,
  })
  const cancel = (u: AdminUser) => cancelDeletion.mutate(u.id, {
    onSuccess: () => toast(t('admin.adminUsersPage.canceledDeletionOf', { username: u.username })), onError: fail,
  })
  const savePrivileges = async (u: AdminUser, body: PrivilegesBody) => {
    await elevated(t('admin.adminUsersPage.adjustPermissions'), () => setPrivileges.mutateAsync({ id: u.id, ...body }))
    toast(t('admin.adminUsersPage.updatedPermissionsFor', { username: u.username }))
  }

  const confirm = pending ? CONFIRM_TEXT[pending.kind](pending.user) : null
  const enabledCount = list.filter(u => u.enabled).length
  const sessionCount = list.reduce((n, u) => n + (u.active_sessions ?? 0), 0)

  return (
    <ConsolePage>
      <PageHeader summary={t('admin.adminUsersPage.createAccountsAdjustRolesAnd')} help={HELP} actions={
        <>
          <ExportCsvButton href={adminCsvUrls.exportUsers()} what={t('admin.adminUsersPage.accountList')} />
          <Button variant="primary" onClick={() => setCreating(true)}><Icon name="plus" size={14} />{t('admin.adminUsersPage.createAccount')}</Button>
        </>
      } />
      <div className={`${c.toolbar} ${chosen.length > 0 ? styles.toolbarSticky : ''}`}>
        <SearchInput value={q} onChange={setQ} placeholder={t('admin.adminUsersPage.filterAccounts')} />
        <Segmented options={filterOptions} value={filter} label={t('admin.adminUsersPage.accountFilter')}
          onChange={v => setFilterParam(v === 'all' ? null : v)} />
        <div className={c.toolbarSpacer} />
        {chosen.length > 0 && (
          <div className={c.bulk} role="toolbar" aria-label={t('admin.adminUsersPage.batchActions')}>
            <span className={styles.bulkCount}>{t('admin.adminUsersPage.selected', { length: chosen.length })}</span>
            {BULK_ACTIONS.map(action => {
              const targets = bulkTargets[action]
              return (
                <Button key={action} size="sm" variant={action === 'disable' ? 'dangerText' : 'default'}
                  disabled={busy || targets.length === 0}
                  title={targets.length === 0 ? t('admin.adminUsersPage.noneOfTheSelectedAccounts', { verb: BULK_TEXT[action].verb }) : undefined}
                  onClick={() => setBulkFor({ action, users: targets })}>
                  {t('admin.adminUsersPage.text', { verb: BULK_TEXT[action].verb, length: targets.length })}
                </Button>
              )
            })}
            <button type="button" className={c.iconBtn} title={t('admin.adminUsersPage.clearSelection')} aria-label={t('admin.adminUsersPage.clearSelection')} onClick={sel.clear}>
              <Icon name="x" size={14} />
            </button>
          </div>
        )}
      </div>
      <BulkResultPanel outcome={outcome} onClose={() => setOutcome(null)} />
      <section className={c.card} aria-label={t('admin.adminUsersPage.accountList')}>
        {users.isPending ? (
          <p className={c.empty}>{t('admin.adminUsersPage.loading')}</p>
        ) : users.isError ? (
          <div className={styles.loadError}><p className={c.error} role="alert">{t('admin.adminUsersPage.couldnTLoadAccounts', { error: messageOf(users.error) })}</p></div>
        ) : list.length === 0 ? (
          <p className={c.empty}>{t('admin.adminUsersPage.noAccountsYet')}</p>
        ) : visible.length === 0 ? (
          <p className={c.empty}>{t('admin.adminUsersPage.noMatchingAccounts')}</p>
        ) : (
          <UsersTable users={visible} myId={myId} canGrant={Boolean(me.data?.is_super)} busy={busy} now={now}
            selected={sel.selected} onToggle={sel.toggle} onToggleAll={sel.setAll}
            ask={(kind, user) => setPending({ kind, user })} promote={promote} enable={enable} cancelDeletion={cancel}
            resetPassword={setResetFor} privileges={setPrivilegesFor} />
        )}
        {users.data && (
          <div className={c.cardFoot}>
            <span className={c.mono}>{t('admin.adminUsersPage.accountsEnabledActiveSessions', { length: visible.length, enabledCount, sessionCount })}</span>
            <span>{t('admin.adminUsersPage.disablingDemotingAndPasswordResets')}</span>
          </div>
        )}
      </section>
      <ConfirmDialog
        open={confirm != null}
        title={confirm?.title ?? ''}
        body={confirm?.body ?? ''}
        confirmLabel={confirm?.label ?? t('admin.adminUsersPage.confirm')}
        onConfirm={() => { if (pending) run(pending) }}
        onCancel={() => setPending(null)}
      />
      <ConfirmDialog
        open={bulkFor != null}
        tone={bulkFor?.action === 'enable' ? 'safe' : 'danger'}
        title={bulkFor ? t('admin.adminUsersPage.accounts', { verb: BULK_TEXT[bulkFor.action].verb, length: bulkFor.users.length }) : ''}
        body={bulkFor
          ? t('admin.adminUsersPage.eachAccountGetsItsOwn', { namesPreview: namesPreview(bulkFor.users.map(u => u.username)), body: BULK_TEXT[bulkFor.action].body })
          : ''}
        confirmLabel={bulkFor ? t('admin.adminUsersPage.text2', { verb: BULK_TEXT[bulkFor.action].verb, length: bulkFor.users.length }) : t('admin.adminUsersPage.confirm')}
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
