import { useState } from 'react'
import {
  Card, Chip, ConfirmDialog, RowActions, Segmented, Status, isElevationCancelled, toast, useElevate,
} from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { ApiError } from '../../../lib/api'
import type { SecuritySessionItem } from '../../../lib/generated/adminApi'
import { useHasScope } from '../../../lib/useMe'
import { fmtDateTime } from '../auditLabels'
import s from './Security.module.css'
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

const SCOPE_OPTIONS = [{ value: 'active', label: '只看有效' }, { value: 'all', label: '全部' }] as const

/** 這個 session 還在權限提升的 10 分鐘視窗內嗎（只是顯示：誰剛重新驗證過）。 */
function isElevated(item: SecuritySessionItem): boolean {
  const until = item.elevated_until ? Date.parse(item.elevated_until) : NaN
  return item.active && !Number.isNaN(until) && until > Date.now()
}

/**
 * 有效 session 總覽與撤銷單一 session（其他裝置不受影響；整個帳號登出在「帳號」頁的強制登出）。
 * 只是顯示層：沒有 accounts.manage 時整張卡不顯示、不送請求；擋人的是後端（accounts.manage＋已提升）。
 * 撤銷先確認（破壞性），再經 `useElevate` 重新驗證身分；結果用 toast 回報。
 */
export function SessionsCard() {
  const canManage = useHasScope('accounts.manage')
  const [activeOnly, setActiveOnly] = useState(true)
  const q = useSecuritySessions(activeOnly, canManage)
  const revoke = useRevokeSession()
  const { elevated, dialog } = useElevate()
  const [confirming, setConfirming] = useState<SecuritySessionItem | null>(null)
  const [busy, setBusy] = useState(false)

  if (!canManage) return null

  const doRevoke = async (target: SecuritySessionItem) => {
    setBusy(true)
    try {
      await elevated('撤銷 session', () => revoke.mutateAsync(target.id))
      toast(target.current ? '已撤銷你目前的 session，下一個請求就會回到登入頁' : `已撤銷 ${target.username} 的 session`)
    } catch (err) {
      if (!isElevationCancelled(err)) toast(messageOf(err), { tone: 'bad' })
    } finally {
      setBusy(false)
      setConfirming(null)
    }
  }

  return (
    <Card title="Session" labelledBy="sec-sessions-title"
      right={<Segmented label="Session 範圍" options={SCOPE_OPTIONS} value={activeOnly ? 'active' : 'all'}
        onChange={v => setActiveOnly(v === 'active')} />}>
      {q.isPending ? (
        <p className={c.empty}>載入中…</p>
      ) : q.isError ? (
        <div className={c.cardBody}><p className={c.error} role="alert">session 載入失敗：{messageOf(q.error)}</p></div>
      ) : q.data.items.length === 0 ? (
        <p className={c.empty}>沒有 session</p>
      ) : (
        <div className={c.tableWrap}>
          <table className={c.table}>
            <thead>
              <tr><th>帳號</th><th>登入時間</th><th>最後活動</th><th>IP</th><th>裝置</th><th>狀態</th><th className={`${c.tdRight} ${c.stickyRight}`}>操作</th></tr>
            </thead>
            <tbody>
              {q.data.items.map(item => (
                <tr key={item.id}>
                  <td>
                    <span className={s.name}>
                      {item.username}
                      {item.current && <Chip tone="gold">目前這個</Chip>}
                      {isElevated(item) && <Chip tone="warn" title={`已重新驗證，到 ${fmtDateTime(item.elevated_until)}`}>已提升</Chip>}
                    </span>
                  </td>
                  <td className={c.num}>{fmtDateTime(item.created_at)}</td>
                  <td className={c.num}>{fmtDateTime(item.last_seen_at)}</td>
                  {/* IPv6 很長：給最大寬度、允許折行（c.num 的 nowrap 由 s.ip 蓋掉）。 */}
                  <td className={`${c.mono} ${s.ip}`}>{item.ip ?? '—'}</td>
                  <td><div className={s.ua} title={item.user_agent ?? undefined}>{shortUa(item.user_agent, 36)}</div></td>
                  <td>
                    {item.active
                      ? <Status tone="ok">有效</Status>
                      : <Status tone="idle">{item.revoked_at ? '已撤銷' : '已過期'}</Status>}
                  </td>
                  <td className={`${c.tdActions} ${c.stickyRight}`}>
                    {item.active && (
                      <RowActions primary={{ label: '撤銷', onClick: () => setConfirming(item) }} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className={s.foot}>撤銷需要近 10 分鐘內重新驗證過密碼；撤銷會寫入操作紀錄。</p>
      <ConfirmDialog open={confirming != null} tone="danger" busy={busy} confirmLabel="撤銷"
        title={confirming ? `撤銷 ${confirming.username} 的這個 session？` : ''}
        body={confirming?.current
          ? '這是你目前的 session：撤銷後下一個請求就會回到登入頁。'
          : '只登出這一個裝置，其他裝置不受影響。'}
        onConfirm={() => { if (confirming) void doRevoke(confirming) }}
        onCancel={() => setConfirming(null)} />
      {dialog}
    </Card>
  )
}
