import { Chip, RowActions, Status, type RowAction } from '../../../components/console'
import { fmtDateTime, roleLabel } from '../auditLabels'
import { SelectAllBox } from '../BulkParts'
import { deletionCountdown } from '../deletionCountdown'
import type { AdminUser } from '../useAdmin'
import type { ConfirmKind } from './userText'
import c from '../../../components/console/Console.module.css'
import styles from './Users.module.css'
import { t } from '@/i18n'

export interface UserRowHandlers {
  /** 要先確認的動作（停用、降級、強制登出、刪除、重設兩步驟驗證）。 */
  ask: (kind: ConfirmKind, user: AdminUser) => void
  promote: (user: AdminUser) => void
  enable: (user: AdminUser) => void
  cancelDeletion: (user: AdminUser) => void
  resetPassword: (user: AdminUser) => void
  privileges: (user: AdminUser) => void
}

interface Props extends UserRowHandlers {
  users: readonly AdminUser[]
  myId: string | null
  /** 登入者是 super admin 才看得到「調整權限」（顯示層；擋人的是後端）。 */
  canGrant: boolean
  /** 有寫入正在進行：整張表的動作先停用，避免連點。 */
  busy: boolean
  now: number
  selected: ReadonlySet<string>
  onToggle: (id: string) => void
  onToggleAll: (ids: readonly string[], on: boolean) => void
}

const busyHint = () => t('admin.users.usersTable.thePreviousActionIsStill')

/**
 * 帳號表格。每列最多一顆主要按鈕＋「更多」：
 * 啟用中→「重設密碼」；停用→「啟用」；排程刪除→只有「取消刪除」（其他操作收起）。
 * 自己那列不給勾，停用、降級、刪除三項停用並說明原因（後端同樣擋）。
 */
export function UsersTable({
  users, myId, canGrant, busy, now, selected, onToggle, onToggleAll, ask, promote, enable, cancelDeletion, resetPassword, privileges,
}: Props) {
  const selectable = users.filter(u => u.id !== myId).map(u => u.id)
  /** 停用的項目一定帶原因：自己的規則優先，其次是「還在忙」。 */
  const gate = (blocked: string | false | undefined): Pick<RowAction, 'disabled' | 'hint'> => {
    const hint = blocked || (busy ? busyHint() : undefined)
    return hint ? { disabled: true, hint } : {}
  }

  return (
    <div className={c.tableWrap}>
      <table className={c.table}>
        <thead>
          <tr>
            <th className={c.thCheck}>
              <SelectAllBox ids={selectable} selected={selected} label={t('admin.users.usersTable.selectAll')} onChange={on => onToggleAll(selectable, on)} />
            </th>
            <th>{t('admin.users.usersTable.username')}</th><th>{t('admin.users.usersTable.role')}</th><th>{t('admin.users.usersTable.status')}</th><th>{t('admin.users.usersTable.lastSignIn')}</th><th>{t('admin.users.usersTable.lastActive')}</th>
            <th className={c.tdRight}>session</th><th className={`${c.tdRight} ${c.stickyRight}`}>{t('admin.users.usersTable.actions')}</th>
          </tr>
        </thead>
        <tbody>
          {users.map(u => {
            const isSelf = myId != null && myId === u.id
            const sessions = u.active_sessions ?? 0
            const deletingAt = u.deletion_execute_after ?? null
            const checked = !isSelf && selected.has(u.id)

            let primary: RowAction
            let more: (RowAction | false)[] = []
            if (deletingAt) {
              primary = { label: t('admin.users.usersTable.cancelDeletion'), onClick: () => cancelDeletion(u), ...gate(false) }
            } else {
              const reset: RowAction = { label: t('admin.users.usersTable.resetPassword'), onClick: () => resetPassword(u), ...gate(false) }
              primary = u.enabled ? reset : { label: t('admin.users.usersTable.enable'), onClick: () => enable(u), ...gate(false) }
              more = [
                u.role === 'admin'
                  ? { label: t('admin.users.usersTable.makeRegularUser'), onClick: () => ask('demote', u), ...gate(isSelf && t('admin.users.usersTable.canTRemoveOneS')) }
                  : { label: t('admin.users.usersTable.makeAdmin'), onClick: () => promote(u), ...gate(false) },
                !u.enabled && reset,
                (u.enabled || sessions > 0) && {
                  label: t('admin.users.usersTable.forceSignOutSessions', { sessions }), onClick: () => ask('logout', u),
                  ...gate(sessions === 0 && t('admin.users.usersTable.noSignedInDevicesRight')),
                },
                canGrant && u.role === 'admin' && { label: t('admin.users.usersTable.adjustPermissions'), onClick: () => privileges(u), ...gate(false) },
                Boolean(u.totp_enabled) && { label: t('admin.users.usersTable.resetTwoStepVerification'), onClick: () => ask('resetTotp', u), ...gate(false) },
                u.enabled && { label: t('admin.users.usersTable.disable'), danger: true, onClick: () => ask('disable', u), ...gate(isSelf && t('admin.users.usersTable.canTDisableOneS')) },
                { label: t('admin.users.usersTable.deleteAccount'), danger: true, onClick: () => ask('delete', u), ...gate(isSelf && t('admin.users.usersTable.canTDeleteOneS')) },
              ]
            }

            return (
              <tr key={u.id} className={checked ? c.rowSelected : undefined}>
                <td className={c.tdCheck}>
                  <input type="checkbox" className={c.check} aria-label={t('admin.users.usersTable.select', { username: u.username })} checked={checked}
                    disabled={isSelf} title={isSelf ? t('admin.users.usersTable.batchActionsCanTInclude') : undefined} onChange={() => onToggle(u.id)} />
                </td>
                <td>
                  <div className={styles.name}>
                    <span className={`${c.truncate} ${styles.username}`} title={u.username}>{u.username}</span>
                    {isSelf && <Chip tone="gold" mono>{t('admin.users.usersTable.me')}</Chip>}
                    {u.is_super && <Chip tone="gold" mono>super</Chip>}
                    {u.totp_enabled && <Chip tone="ok" mono title={t('admin.users.usersTable.twoStepVerificationOn')}>2FA</Chip>}
                    {u.sso_bound && <Chip>{t('admin.users.usersTable.companyAccountSso')}</Chip>}
                  </div>
                </td>
                <td className={`${c.t3} ${c.nowrap}`}>{roleLabel(u.role)}</td>
                <td>
                  {deletingAt ? (
                    <>
                      <Status tone="bad">{t('admin.users.usersTable.deletionScheduled')}</Status>
                      <div className={styles.countdown} title={t('admin.users.usersTable.runsAt', { deletingAt: fmtDateTime(deletingAt) })}>
                        {deletionCountdown(deletingAt, now)}
                      </div>
                    </>
                  ) : (
                    <Status tone={u.enabled ? 'ok' : 'idle'}>{u.enabled ? t('admin.users.usersTable.enable') : t('admin.users.usersTable.disable')}</Status>
                  )}
                </td>
                <td className={`${c.num} ${c.t3}`}>{fmtDateTime(u.last_login_at)}</td>
                <td className={`${c.num} ${c.muted}`}>{fmtDateTime(u.last_seen_at)}</td>
                <td className={`${c.num} ${c.tdRight} ${sessions ? c.t3 : c.muted}`} title={t('admin.users.usersTable.activeSessions')}>{sessions}</td>
                <td className={`${c.tdActions} ${c.stickyRight}`}>
                  <RowActions primary={primary} more={more} label={t('admin.users.usersTable.moreActionsFor', { username: u.username })} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
