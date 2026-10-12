import { useState } from 'react'
import { ApiError } from '../../../lib/api'
import { adminApi } from '../../../lib/generated/adminApi'
import { useHasScope, useMe } from '../../../lib/useMe'
import { ElevationCancelledError, useElevationGate } from '../../account/useElevationGate'
import { fmtDateTime } from '../auditLabels'
import adminStyles from '../Admin.module.css'
import styles from './Security.module.css'
import { shortUa } from './securityLabels'
import { useRevokeSession, useSecuritySessions } from './useSecurity'

function messageOf(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'super_required') return 'super admin 的 session 只有 super admin 能撤銷。'
    if (err.code === 'not_found') return '找不到這個 session（可能已被清除），請重新整理。'
    if (err.code === 'account_deleted') return '帳號已刪除。'
    if (err.code === 'missing_scope') return '需要「accounts.manage」權限。'
  }
  return err instanceof Error && err.message ? err.message : '操作失敗，請重試'
}

/**
 * 有效 session 總覽與撤銷單一 session（其他裝置不受影響；整個帳號登出在「帳號」頁的強制登出）。
 * 只是顯示層：沒有 accounts.manage 時整張卡不顯示、不送請求；擋人的是後端（accounts.manage＋已提升）。
 * 需要重新驗證時由 useElevationGate 彈出驗證框、驗證後自動重試。撤銷前先在列上確認一次。
 */
export function SessionsCard() {
  const canManage = useHasScope('accounts.manage')
  const me = useMe()
  const [activeOnly, setActiveOnly] = useState(true)
  const q = useSecuritySessions(activeOnly, canManage)
  const revoke = useRevokeSession()
  const { guard, dialog } = useElevationGate(adminApi.elevate, Boolean(me.data?.totp_enabled))
  const [confirming, setConfirming] = useState<string | null>(null)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  if (!canManage) return null

  const doRevoke = async (id: string, username: string, current: boolean) => {
    setConfirming(null)
    setMessage(null)
    try {
      await guard(() => revoke.mutateAsync(id))
      setMessage({ ok: true, text: current ? '已撤銷你目前的 session，下一個請求就會回到登入頁。' : `已撤銷 ${username} 的這個 session。` })
    } catch (err) {
      if (err instanceof ElevationCancelledError) return
      setMessage({ ok: false, text: messageOf(err) })
    }
  }

  return (
    <section className={adminStyles.card} aria-labelledby="sec-sessions-title">
      <div className={adminStyles.cardHead}>
        <h2 id="sec-sessions-title" className={adminStyles.ctitle}>Session</h2>
        <label className={adminStyles.check}>
          <input type="checkbox" checked={activeOnly} onChange={e => setActiveOnly(e.target.checked)} />
          只看有效的
        </label>
      </div>
      {message && (
        <p className={message.ok ? adminStyles.ok : adminStyles.error} role={message.ok ? 'status' : 'alert'}>{message.text}</p>
      )}
      {q.isPending ? (
        <p className={adminStyles.idle}>載入中…</p>
      ) : q.isError ? (
        <p className={adminStyles.error} role="alert">session 載入失敗：{messageOf(q.error)}</p>
      ) : q.data.items.length === 0 ? (
        <p className={adminStyles.idle}>沒有 session</p>
      ) : (
        <div className={adminStyles.tableWrap}>
          <table className={adminStyles.table}>
            <thead>
              <tr><th>帳號</th><th>登入時間</th><th>最後活動</th><th>IP</th><th>裝置</th><th>狀態</th><th /></tr>
            </thead>
            <tbody>
              {q.data.items.map(s => (
                <tr key={s.id}>
                  <td>
                    {s.username}
                    {s.current && <span className={adminStyles.self}>（目前這個）</span>}
                  </td>
                  <td className={adminStyles.num}>{fmtDateTime(s.created_at)}</td>
                  <td className={adminStyles.num}>{fmtDateTime(s.last_seen_at)}</td>
                  <td className={styles.mono}>{s.ip ?? '—'}</td>
                  <td className={adminStyles.muted} title={s.user_agent ?? undefined}>{shortUa(s.user_agent, 36)}</td>
                  <td>
                    {s.active
                      ? <span className={adminStyles.badge}>有效</span>
                      : <span className={`${adminStyles.badge} ${adminStyles.badgeOff}`}>{s.revoked_at ? '已撤銷' : '已過期'}</span>}
                  </td>
                  <td className={adminStyles.actionsCell}>
                    {s.active && (confirming === s.id ? (
                      <span className={styles.confirm} role="group" aria-label={`確認撤銷 ${s.username} 的 session`}>
                        <button type="button" className={`${adminStyles.action} ${adminStyles.danger}`} disabled={revoke.isPending}
                          onClick={() => void doRevoke(s.id, s.username, Boolean(s.current))}>確定撤銷</button>
                        <button type="button" className={adminStyles.action} onClick={() => setConfirming(null)}>取消</button>
                      </span>
                    ) : (
                      <button type="button" className={adminStyles.action} onClick={() => setConfirming(s.id)}>撤銷</button>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className={adminStyles.hint}>撤銷需要近 10 分鐘內重新驗證過密碼；撤銷會寫入操作紀錄。</p>
      {dialog}
    </section>
  )
}
