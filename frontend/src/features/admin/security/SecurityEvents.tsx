import { useState } from 'react'
import { useDebouncedValue } from '../../../lib/useDebouncedValue'
import { fmtDateTime } from '../auditLabels'
import adminStyles from '../Admin.module.css'
import styles from './Security.module.css'
import { EVENT_OPTIONS, eventLabel, shortUa } from './securityLabels'
import { EVENTS_PAGE_SIZE, type AuthEventType, useAuthEvents, useSuspiciousIps } from './useSecurity'

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '載入失敗，請重試'
}

/** 登入與安全事件。事件本身沒有帳號名稱；顯示的名稱是讀取時對帳號現值 join 的（已刪除帳號不顯示）。 */
export function AuthEventsCard({ ipFilter, onIpFilter }: { ipFilter: string; onIpFilter: (ip: string) => void }) {
  const [event, setEvent] = useState<AuthEventType | ''>('')
  // keyset 分頁：堆疊記住每一頁的 before_id，上一頁就是彈出。
  const [cursors, setCursors] = useState<(number | null)[]>([null])
  const ip = useDebouncedValue(ipFilter, 400)
  // IP 篩選可能由外部（可疑 IP 的「看事件」）改變：條件一變就回到第一頁。
  const [pagedIp, setPagedIp] = useState(ip)
  if (pagedIp !== ip) {
    setPagedIp(ip)
    setCursors([null])
  }
  const beforeId = cursors[cursors.length - 1]
  const q = useAuthEvents({ event, ip, beforeId })
  const resetPaging = () => setCursors([null])

  return (
    <section className={adminStyles.card} aria-labelledby="sec-events-title">
      <h2 id="sec-events-title" className={adminStyles.ctitle}>登入事件</h2>
      <div className={styles.filters}>
        <label className={adminStyles.field}>
          類型
          <select value={event} onChange={e => { setEvent(e.target.value as AuthEventType | ''); resetPaging() }}>
            <option value="">全部</option>
            {EVENT_OPTIONS.map(v => <option key={v} value={v}>{eventLabel(v)}</option>)}
          </select>
        </label>
        <label className={adminStyles.field}>
          IP
          <input value={ipFilter} maxLength={64} placeholder="完整 IP"
            onChange={e => onIpFilter(e.target.value)} />
        </label>
      </div>
      {q.isPending ? (
        <p className={adminStyles.idle}>載入中…</p>
      ) : q.isError ? (
        <p className={adminStyles.error} role="alert">登入事件載入失敗：{messageOf(q.error)}</p>
      ) : q.data.items.length === 0 ? (
        <p className={adminStyles.idle}>沒有符合條件的事件</p>
      ) : (
        <>
          <div className={adminStyles.tableWrap}>
            <table className={adminStyles.table}>
              <thead><tr><th>時間</th><th>事件</th><th>帳號</th><th>IP</th><th>次數</th><th>裝置</th></tr></thead>
              <tbody>
                {q.data.items.map(e => (
                  <tr key={e.id}>
                    <td className={adminStyles.num}>{fmtDateTime(e.occurred_at)}</td>
                    <td>{eventLabel(e.event, e.reason)}</td>
                    <td className={e.username ? undefined : adminStyles.muted}>
                      {e.username ?? (e.user_id ? '（已刪除）' : '—')}
                    </td>
                    <td className={styles.mono}>{e.ip ?? '—'}</td>
                    <td className={adminStyles.num}>{e.count}</td>
                    <td className={adminStyles.muted} title={e.user_agent ?? undefined}>{shortUa(e.user_agent)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className={adminStyles.pager}>
            <button type="button" className={adminStyles.action} disabled={cursors.length <= 1}
              onClick={() => setCursors(cursors.slice(0, -1))}>較新</button>
            <span>{`第 ${cursors.length} 頁（每頁 ${EVENTS_PAGE_SIZE} 筆）`}</span>
            <button type="button" className={adminStyles.action} disabled={q.data.next_before_id == null}
              onClick={() => { if (q.data.next_before_id != null) setCursors([...cursors, q.data.next_before_id]) }}>
              較舊
            </button>
          </div>
          <p className={adminStyles.hint}>「登入被限流」「非 HTTPS 登入被拒」與「驗證逾時」是彙總列：次數＝那段期間被擋下的請求數。</p>
        </>
      )}
    </section>
  )
}

const HOURS = [1, 6, 24, 72, 168] as const

/** 可疑 IP：只彙整，不封鎖（封鎖交給 Cloudflare WAF，操作手冊在 docs/production_resilience.md）。 */
export function SuspiciousIpsCard({ onPick }: { onPick: (ip: string) => void }) {
  const [hours, setHours] = useState<number>(24)
  const q = useSuspiciousIps(hours)
  return (
    <section className={adminStyles.card} aria-labelledby="sec-ips-title">
      <div className={adminStyles.cardHead}>
        <h2 id="sec-ips-title" className={adminStyles.ctitle}>可疑 IP</h2>
        <label className={adminStyles.field}>
          期間
          <select value={hours} onChange={e => setHours(Number(e.target.value))}>
            {HOURS.map(h => <option key={h} value={h}>{h < 24 ? `${h} 小時` : `${h / 24} 天`}</option>)}
          </select>
        </label>
      </div>
      {q.isPending ? (
        <p className={adminStyles.idle}>載入中…</p>
      ) : q.isError ? (
        <p className={adminStyles.error} role="alert">可疑 IP 載入失敗：{messageOf(q.error)}</p>
      ) : q.data.items.length === 0 ? (
        <p className={adminStyles.idle}>{`這段期間沒有失敗達 ${q.data.min_failures} 次的 IP`}</p>
      ) : (
        <div className={adminStyles.tableWrap}>
          <table className={adminStyles.table}>
            <thead>
              <tr><th>IP</th><th>失敗</th><th>被限流</th><th>非 HTTPS</th><th>成功</th><th>涉及帳號</th><th>最後出現</th><th /></tr>
            </thead>
            <tbody>
              {q.data.items.map(r => (
                <tr key={r.ip}>
                  <td className={styles.mono}>{r.ip}</td>
                  <td className={adminStyles.num}>{r.failures}</td>
                  <td className={adminStyles.num}>{r.locked}</td>
                  <td className={adminStyles.num}>{r.insecure}</td>
                  <td className={adminStyles.num}>{r.successes}</td>
                  <td className={adminStyles.num}>{r.distinct_users}</td>
                  <td className={adminStyles.num}>{fmtDateTime(r.last_seen)}</td>
                  <td>
                    <button type="button" className={adminStyles.action} onClick={() => onPick(r.ip)}>看事件</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className={adminStyles.hint}>
        本站不做帳號鎖定或 IP 封鎖；要擋某個 IP，請在 Cloudflare WAF 加自訂規則（步驟見維運文件「Cloudflare WAF 自訂規則」）。
      </p>
    </section>
  )
}
