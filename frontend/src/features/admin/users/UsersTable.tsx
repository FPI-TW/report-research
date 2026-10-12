import { Chip, RowActions, Status, type RowAction } from '../../../components/console'
import { fmtDateTime, roleLabel } from '../auditLabels'
import { SelectAllBox } from '../BulkParts'
import { deletionCountdown } from '../deletionCountdown'
import type { AdminUser } from '../useAdmin'
import type { ConfirmKind } from './userText'
import c from '../../../components/console/Console.module.css'
import styles from './Users.module.css'

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

const BUSY_HINT = '上一個操作還在進行'

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
    const hint = blocked || (busy ? BUSY_HINT : undefined)
    return hint ? { disabled: true, hint } : {}
  }

  return (
    <div className={c.tableWrap}>
      <table className={c.table}>
        <thead>
          <tr>
            <th className={c.thCheck}>
              <SelectAllBox ids={selectable} selected={selected} label="全選" onChange={on => onToggleAll(selectable, on)} />
            </th>
            <th>帳號</th><th>角色</th><th>狀態</th><th>最後登入</th><th>最後活動</th>
            <th className={c.tdRight}>session</th><th className={`${c.tdRight} ${c.stickyRight}`}>操作</th>
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
              primary = { label: '取消刪除', onClick: () => cancelDeletion(u), ...gate(false) }
            } else {
              const reset: RowAction = { label: '重設密碼', onClick: () => resetPassword(u), ...gate(false) }
              primary = u.enabled ? reset : { label: '啟用', onClick: () => enable(u), ...gate(false) }
              more = [
                u.role === 'admin'
                  ? { label: '改為一般使用者', onClick: () => ask('demote', u), ...gate(isSelf && '不能拿掉自己的管理員權限') }
                  : { label: '設為管理員', onClick: () => promote(u), ...gate(false) },
                !u.enabled && reset,
                (u.enabled || sessions > 0) && {
                  label: `強制登出（${sessions} 個 session）`, onClick: () => ask('logout', u),
                  ...gate(sessions === 0 && '目前沒有已登入的裝置'),
                },
                canGrant && u.role === 'admin' && { label: '調整權限', onClick: () => privileges(u), ...gate(false) },
                Boolean(u.totp_enabled) && { label: '重設兩步驟驗證', onClick: () => ask('resetTotp', u), ...gate(false) },
                u.enabled && { label: '停用', danger: true, onClick: () => ask('disable', u), ...gate(isSelf && '不能停用自己') },
                { label: '刪除帳號', danger: true, onClick: () => ask('delete', u), ...gate(isSelf && '不能刪除自己的帳號') },
              ]
            }

            return (
              <tr key={u.id} className={checked ? c.rowSelected : undefined}>
                <td className={c.tdCheck}>
                  <input type="checkbox" className={c.check} aria-label={`選取「${u.username}」`} checked={checked}
                    disabled={isSelf} title={isSelf ? '不能對自己做批次操作' : undefined} onChange={() => onToggle(u.id)} />
                </td>
                <td>
                  <div className={styles.name}>
                    <span className={`${c.truncate} ${styles.username}`} title={u.username}>{u.username}</span>
                    {isSelf && <Chip tone="gold" mono>你</Chip>}
                    {u.is_super && <Chip tone="gold" mono>super</Chip>}
                    {u.totp_enabled && <Chip tone="ok" mono title="已開啟兩步驟驗證">2FA</Chip>}
                  </div>
                </td>
                <td className={`${c.t3} ${c.nowrap}`}>{roleLabel(u.role)}</td>
                <td>
                  {deletingAt ? (
                    <>
                      <Status tone="bad">排程刪除</Status>
                      <div className={styles.countdown} title={`預定 ${fmtDateTime(deletingAt)} 執行`}>
                        {deletionCountdown(deletingAt, now)}
                      </div>
                    </>
                  ) : (
                    <Status tone={u.enabled ? 'ok' : 'idle'}>{u.enabled ? '啟用' : '停用'}</Status>
                  )}
                </td>
                <td className={`${c.num} ${c.t3}`}>{fmtDateTime(u.last_login_at)}</td>
                <td className={`${c.num} ${c.muted}`}>{fmtDateTime(u.last_seen_at)}</td>
                <td className={`${c.num} ${c.tdRight} ${sessions ? c.t3 : c.muted}`} title="有效 session 數">{sessions}</td>
                <td className={`${c.tdActions} ${c.stickyRight}`}>
                  <RowActions primary={primary} more={more} label={`${u.username} 的更多操作`} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
