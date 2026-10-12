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

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '載入失敗，請重試'
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
    <Card title="登入事件" labelledBy="sec-events-title">
      <div className={s.filters}>
        <label className={s.inlineField}>
          類型
          <select className={c.select} value={event}
            onChange={e => { setEvent(e.target.value as AuthEventType | ''); setCursors([null]) }}>
            <option value="">全部</option>
            {EVENT_OPTIONS.map(v => <option key={v} value={v}>{eventLabel(v)}</option>)}
          </select>
        </label>
        <label className={s.inlineField}>
          IP
          <input className={`${c.input} ${c.inputMono} ${s.ipInput}`} value={ip.draft} maxLength={64} placeholder="完整 IP"
            autoComplete="off" spellCheck={false} onChange={e => ip.setDraft(e.target.value)} />
        </label>
        {ip.applied && (
          <span className={s.filterChip}>
            IP {ip.applied}
            <button type="button" className={s.filterChipClear} onClick={ip.clear} title="清除 IP 篩選" aria-label="清除 IP 篩選">
              <Icon name="x" size={12} />
            </button>
          </span>
        )}
      </div>
      {q.isPending ? (
        <p className={c.empty}>載入中…</p>
      ) : q.isError ? (
        <div className={c.cardBody}><p className={c.error} role="alert">登入事件載入失敗：{messageOf(q.error)}</p></div>
      ) : (
        <>
          {q.data.items.length === 0 ? (
            <p className={c.empty}>沒有符合條件的事件</p>
          ) : (
            <div className={c.tableWrap}>
              <table className={c.table}>
                <thead><tr><th>時間</th><th>事件</th><th>帳號</th><th>IP</th><th className={c.tdRight}>次數</th><th>裝置</th></tr></thead>
                <tbody>
                  {q.data.items.map(e => (
                    <tr key={e.id}>
                      <td className={c.num}>{fmtDateTime(e.occurred_at)}</td>
                      <td><span className={s.tone} data-tone={eventTone(e.event)}>{eventLabel(e.event, e.reason)}</span></td>
                      <td className={e.username ? c.t1 : c.muted}>
                        {e.username ?? (e.user_id ? '（已刪除）' : '—')}
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
            <KeysetPager page={cursors.length} note={`（每頁 ${EVENTS_PAGE_SIZE} 筆）`}
              hasOlder={q.data.next_before_id != null}
              onNewer={() => setCursors(cursors.slice(0, -1))}
              onOlder={() => { if (q.data.next_before_id != null) setCursors([...cursors, q.data.next_before_id]) }} />
          )}
        </>
      )}
      <p className={s.foot}>「登入被限流」「非 HTTPS 登入被拒」與「驗證逾時」是彙總列：次數＝那段期間被擋下的請求數。</p>
    </Card>
  )
}

const HOURS = [1, 6, 24, 72, 168] as const
const HOUR_OPTIONS = HOURS.map(h => ({ value: String(h), label: h < 24 ? `${h} 小時` : `${h / 24} 天` }))

/** 可疑 IP：只彙整，不封鎖（封鎖交給 Cloudflare WAF，操作手冊在 docs/production_resilience.md）。 */
export function SuspiciousIpsCard({ onPick }: { onPick: (ip: string) => void }) {
  const [hours, setHours] = useState<number>(24)
  const q = useSuspiciousIps(hours)

  const copy = (ip: string) => {
    copyText(ip).then(() => toast(`已複製 ${ip}`), () => toast('複製失敗，請手動複製', { tone: 'bad' }))
  }

  return (
    <Card title="可疑 IP" labelledBy="sec-ips-title" hint="只彙整，不封鎖"
      right={<Segmented label="期間" options={HOUR_OPTIONS} value={String(hours)} onChange={v => setHours(Number(v))} />}>
      {q.isPending ? (
        <p className={c.empty}>載入中…</p>
      ) : q.isError ? (
        <div className={c.cardBody}><p className={c.error} role="alert">可疑 IP 載入失敗：{messageOf(q.error)}</p></div>
      ) : q.data.items.length === 0 ? (
        <p className={c.empty}>{`這段期間沒有失敗達 ${q.data.min_failures} 次的 IP`}</p>
      ) : (
        <div className={c.tableWrap}>
          <table className={c.table}>
            <thead>
              <tr>
                <th>IP</th><th className={c.tdRight}>失敗</th><th className={c.tdRight}>被限流</th>
                <th className={c.tdRight}>非 HTTPS</th><th className={c.tdRight}>成功</th><th className={c.tdRight}>涉及帳號</th>
                <th>最後出現</th><th className={`${c.tdRight} ${c.stickyRight}`}>操作</th>
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
                    <RowActions label={`${r.ip} 的更多操作`}
                      primary={{ label: '看事件', onClick: () => onPick(r.ip) }}
                      more={[{ label: '複製 IP', onClick: () => copy(r.ip) }]} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className={s.foot}>
        本站不做帳號鎖定或 IP 封鎖；要擋某個 IP，請在 Cloudflare WAF 加自訂規則（步驟見維運文件「Cloudflare WAF 自訂規則」）。
      </p>
    </Card>
  )
}
