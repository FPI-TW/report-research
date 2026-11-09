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
import { t } from '@/i18n'

function messageOf(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'super_required') return t('admin.security.securitySessions.onlyASuperAdminCan')
    if (err.code === 'not_found') return t('admin.security.securitySessions.sessionNotFoundItMay')
    if (err.code === 'account_deleted') return t('admin.security.securitySessions.accountDeleted')
    if (err.code === 'missing_scope') return t('admin.security.securitySessions.requiresTheAccountsManagePermission')
  }
  return err instanceof Error && err.message ? err.message : t('admin.security.securitySessions.actionFailedTryAgain')
}

const SCOPE_OPTIONS = [{ value: 'active', get label() { return t('admin.security.securitySessions.activeOnly') } }, { value: 'all', get label() { return t('admin.security.securitySessions.all') } }] as const

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
      await elevated(t('admin.security.securitySessions.revokeSession'), () => revoke.mutateAsync(target.id))
      toast(target.current ? t('admin.security.securitySessions.revokedTheCurrentSessionThe') : t('admin.security.securitySessions.revokedSSession', { username: target.username }))
    } catch (err) {
      if (!isElevationCancelled(err)) toast(messageOf(err), { tone: 'bad' })
    } finally {
      setBusy(false)
      setConfirming(null)
    }
  }

  return (
    <Card title="Session" labelledBy="sec-sessions-title"
      right={<Segmented label={t('admin.security.securitySessions.sessionScope')} options={SCOPE_OPTIONS} value={activeOnly ? 'active' : 'all'}
        onChange={v => setActiveOnly(v === 'active')} />}>
      {q.isPending ? (
        <p className={c.empty}>{t('admin.security.securitySessions.loading')}</p>
      ) : q.isError ? (
        <div className={c.cardBody}><p className={c.error} role="alert">{t('admin.security.securitySessions.couldnTLoadSessions', { error: messageOf(q.error) })}</p></div>
      ) : q.data.items.length === 0 ? (
        <p className={c.empty}>{t('admin.security.securitySessions.noSessions')}</p>
      ) : (
        <div className={c.tableWrap}>
          <table className={c.table}>
            <thead>
              <tr><th>{t('admin.security.securitySessions.account')}</th><th>{t('admin.security.securitySessions.signedIn')}</th><th>{t('admin.security.securitySessions.lastActive')}</th><th>IP</th><th>{t('admin.security.securitySessions.device')}</th><th>{t('admin.security.securitySessions.status')}</th><th className={`${c.tdRight} ${c.stickyRight}`}>{t('admin.security.securitySessions.actions')}</th></tr>
            </thead>
            <tbody>
              {q.data.items.map(item => (
                <tr key={item.id}>
                  <td>
                    <span className={s.name}>
                      {item.username}
                      {item.current && <Chip tone="gold">{t('admin.security.securitySessions.thisOne')}</Chip>}
                      {isElevated(item) && <Chip tone="warn" title={t('admin.security.securitySessions.reVerifiedUntil', { elevated_until: fmtDateTime(item.elevated_until) })}>{t('admin.security.securitySessions.elevated')}</Chip>}
                    </span>
                  </td>
                  <td className={c.num}>{fmtDateTime(item.created_at)}</td>
                  <td className={c.num}>{fmtDateTime(item.last_seen_at)}</td>
                  {/* IPv6 很長：給最大寬度、允許折行（c.num 的 nowrap 由 s.ip 蓋掉）。 */}
                  <td className={`${c.mono} ${s.ip}`}>{item.ip ?? '—'}</td>
                  <td><div className={s.ua} title={item.user_agent ?? undefined}>{shortUa(item.user_agent, 36)}</div></td>
                  <td>
                    {item.active
                      ? <Status tone="ok">{t('admin.security.securitySessions.active')}</Status>
                      : <Status tone="idle">{item.revoked_at ? t('admin.security.securitySessions.revoked') : t('admin.security.securitySessions.expired')}</Status>}
                  </td>
                  <td className={`${c.tdActions} ${c.stickyRight}`}>
                    {item.active && (
                      <RowActions primary={{ label: t('admin.security.securitySessions.revoke'), onClick: () => setConfirming(item) }} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className={s.foot}>{t('admin.security.securitySessions.revokingRequiresAPasswordRe')}</p>
      <ConfirmDialog open={confirming != null} tone="danger" busy={busy} confirmLabel={t('admin.security.securitySessions.revoke')}
        title={confirming ? t('admin.security.securitySessions.revokeThisSessionOf', { username: confirming.username }) : ''}
        body={confirming?.current
          ? t('admin.security.securitySessions.thisIsTheCurrentSession')
          : t('admin.security.securitySessions.signsOutOnlyThisDevice')}
        onConfirm={() => { if (confirming) void doRevoke(confirming) }}
        onCancel={() => setConfirming(null)} />
      {dialog}
    </Card>
  )
}
