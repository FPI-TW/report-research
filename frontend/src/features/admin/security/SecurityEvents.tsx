import { useState } from 'react'
import { Card, RowActions, Segmented, toast } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { Icon } from '../../../components/primitives/Icon'
import { copyText } from '../../../lib/clipboard'
import { fmtDateTime } from '../auditLabels'
import { KeysetPager } from './KeysetPager'
import s from './Security.module.css'
import { EVENT_OPTIONS, eventLabel, eventTone, shortUa } from './securityLabels'
import { EVENTS_PAGE_SIZE, type AuthEventType, useAuthEvents, useSuspiciousIps } from './useSecurity'
import { t } from '@/i18n'

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : t('admin.security.securityEvents.couldnTLoadTryAgain')
}

export interface IpFilter {
  /** 套用中的 IP（URL 的 `?ip=`）；空字串＝沒有篩選。 */
  applied: string
  /** 輸入框裡的字（停手後才套用）。 */
  draft: string
  setDraft: (v: string) => void
  clear: () => void
}

/** 登入與安全事件。事件本身沒有帳號名稱；顯示的名稱是讀取時對帳號現值 join 的（已刪除帳號不顯示）。 */
export function AuthEventsCard({ ip }: { ip: IpFilter }) {
  const [event, setEvent] = useState<AuthEventType | ''>('')
  // keyset 分頁：堆疊記住每一頁的 before_id，上一頁就是彈出。
  const [cursors, setCursors] = useState<(number | null)[]>([null])
  // IP 篩選可能由外部（可疑 IP 的「看事件」、清除標籤、上一頁）改變：條件一變就回到第一頁。
  const [pagedIp, setPagedIp] = useState(ip.applied)
  if (pagedIp !== ip.applied) {
    setPagedIp(ip.applied)
    setCursors([null])
  }
  const q = useAuthEvents({ event, ip: ip.applied, beforeId: cursors[cursors.length - 1] })

  return (
    <Card title={t('admin.security.securityEvents.signInEvents')} labelledBy="sec-events-title">
      <div className={s.filters}>
        <label className={s.inlineField}>
          {t('admin.security.securityEvents.type')}
          <select className={c.select} value={event}
            onChange={e => { setEvent(e.target.value as AuthEventType | ''); setCursors([null]) }}>
            <option value="">{t('admin.security.securityEvents.all')}</option>
            {EVENT_OPTIONS.map(v => <option key={v} value={v}>{eventLabel(v)}</option>)}
          </select>
        </label>
        <label className={s.inlineField}>
          IP
          <input className={`${c.input} ${c.inputMono} ${s.ipInput}`} value={ip.draft} maxLength={64} placeholder={t('admin.security.securityEvents.fullIp')}
            autoComplete="off" spellCheck={false} onChange={e => ip.setDraft(e.target.value)} />
        </label>
        {ip.applied && (
          <span className={s.filterChip}>
            IP {ip.applied}
            <button type="button" className={s.filterChipClear} onClick={ip.clear} title={t('admin.security.securityEvents.clearIpFilter')} aria-label={t('admin.security.securityEvents.clearIpFilter')}>
              <Icon name="x" size={12} />
            </button>
          </span>
        )}
      </div>
      {q.isPending ? (
        <p className={c.empty}>{t('admin.security.securityEvents.loading')}</p>
      ) : q.isError ? (
        <div className={c.cardBody}><p className={c.error} role="alert">{t('admin.security.securityEvents.couldnTLoadSignIn', { error: messageOf(q.error) })}</p></div>
      ) : (
        <>
          {q.data.items.length === 0 ? (
            <p className={c.empty}>{t('admin.security.securityEvents.noMatchingEvents')}</p>
          ) : (
            <div className={c.tableWrap}>
              <table className={c.table}>
                <thead><tr><th>{t('admin.security.securityEvents.time')}</th><th>{t('admin.security.securityEvents.event')}</th><th>{t('admin.security.securityEvents.account')}</th><th>IP</th><th className={c.tdRight}>{t('admin.security.securityEvents.count')}</th><th>{t('admin.security.securityEvents.device')}</th></tr></thead>
                <tbody>
                  {q.data.items.map(e => (
                    <tr key={e.id}>
                      <td className={c.num}>{fmtDateTime(e.occurred_at)}</td>
                      <td><span className={s.tone} data-tone={eventTone(e.event)}>{eventLabel(e.event, e.reason)}</span></td>
                      <td className={e.username ? c.t1 : c.muted}>
                        {e.username ?? (e.user_id ? t('admin.security.securityEvents.deleted') : '—')}
                      </td>
                      <td className={c.num}>{e.ip ?? '—'}</td>
                      <td className={`${c.num} ${c.tdRight}`}>{e.count}</td>
                      <td><div className={s.ua} title={e.user_agent ?? undefined}>{shortUa(e.user_agent)}</div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {/* 翻到空的那一頁也要能翻回來：只要不在第一頁就留著頁尾。 */}
          {(q.data.items.length > 0 || cursors.length > 1) && (
            <KeysetPager page={cursors.length} note={t('admin.security.securityEvents.perPage', { EVENTS_PAGE_SIZE })}
              hasOlder={q.data.next_before_id != null}
              onNewer={() => setCursors(cursors.slice(0, -1))}
              onOlder={() => { if (q.data.next_before_id != null) setCursors([...cursors, q.data.next_before_id]) }} />
          )}
        </>
      )}
      <p className={s.foot}>{t('admin.security.securityEvents.signInRateLimitedNon')}</p>
    </Card>
  )
}

const HOURS = [1, 6, 24, 72, 168] as const
const HOUR_OPTIONS = HOURS.map(h => ({ value: String(h), label: h < 24 ? t('admin.security.securityEvents.h', { h }) : t('admin.security.securityEvents.days', { v: h / 24 }) }))

/** 可疑 IP：只彙整，不封鎖（封鎖交給 Cloudflare WAF，操作手冊在 docs/production_resilience.md）。 */
export function SuspiciousIpsCard({ onPick }: { onPick: (ip: string) => void }) {
  const [hours, setHours] = useState<number>(24)
  const q = useSuspiciousIps(hours)

  const copy = (ip: string) => {
    copyText(ip).then(() => toast(t('admin.security.securityEvents.copied', { ip })), () => toast(t('admin.security.securityEvents.copyFailedCopyManually'), { tone: 'bad' }))
  }

  return (
    <Card title={t('admin.security.securityEvents.suspiciousIps')} labelledBy="sec-ips-title" hint={t('admin.security.securityEvents.aggregatedOnlyNeverBlocked')}
      right={<Segmented label={t('admin.security.securityEvents.period')} options={HOUR_OPTIONS} value={String(hours)} onChange={v => setHours(Number(v))} />}>
      {q.isPending ? (
        <p className={c.empty}>{t('admin.security.securityEvents.loading')}</p>
      ) : q.isError ? (
        <div className={c.cardBody}><p className={c.error} role="alert">{t('admin.security.securityEvents.couldnTLoadSuspiciousIps', { error: messageOf(q.error) })}</p></div>
      ) : q.data.items.length === 0 ? (
        <p className={c.empty}>{t('admin.security.securityEvents.noIpsWithAtLeast', { min_failures: q.data.min_failures })}</p>
      ) : (
        <div className={c.tableWrap}>
          <table className={c.table}>
            <thead>
              <tr>
                <th>IP</th><th className={c.tdRight}>{t('admin.security.securityEvents.failed')}</th><th className={c.tdRight}>{t('admin.security.securityEvents.rateLimited')}</th>
                <th className={c.tdRight}>{t('admin.security.securityEvents.nonHttps')}</th><th className={c.tdRight}>{t('admin.security.securityEvents.succeeded')}</th><th className={c.tdRight}>{t('admin.security.securityEvents.accountsInvolved')}</th>
                <th>{t('admin.security.securityEvents.lastSeen')}</th><th className={`${c.tdRight} ${c.stickyRight}`}>{t('admin.security.securityEvents.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {q.data.items.map(r => (
                <tr key={r.ip}>
                  <td className={`${c.num} ${c.t1}`}>{r.ip}</td>
                  <td className={`${c.num} ${c.tdRight}`}>{r.failures}</td>
                  <td className={`${c.num} ${c.tdRight}`}>{r.locked}</td>
                  <td className={`${c.num} ${c.tdRight}`}>{r.insecure}</td>
                  <td className={`${c.num} ${c.tdRight}`}>{r.successes}</td>
                  <td className={`${c.num} ${c.tdRight}`}>{r.distinct_users}</td>
                  <td className={c.num}>{fmtDateTime(r.last_seen)}</td>
                  <td className={`${c.tdActions} ${c.stickyRight}`}>
                    <RowActions label={t('admin.security.securityEvents.moreActionsFor', { ip: r.ip })}
                      primary={{ label: t('admin.security.securityEvents.viewEvents'), onClick: () => onPick(r.ip) }}
                      more={[{ label: t('admin.security.securityEvents.copyIp'), onClick: () => copy(r.ip) }]} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className={s.foot}>
        {t('admin.security.securityEvents.thisSiteDoesnTLock')}
      </p>
    </Card>
  )
}
