import { useState, type FormEvent } from 'react'
import { Modal } from '../../../components/primitives/Modal'
import { RequireAdmin } from '../../../components/shell/RequireAdmin'
import { adminApi, type QuotaItem, type QuotaOverview, type QuotaUserRow } from '../../../lib/generated/adminApi'
import { useMe } from '../../../lib/useMe'
import { ElevationCancelledError, useElevationGate } from '../../account/useElevationGate'
import { AdminHeader } from '../AdminHeader'
import { roleLabel } from '../auditLabels'
import { RequireScope } from '../RequireScope'
import adminStyles from '../Admin.module.css'
import opsStyles from '../ops/Ops.module.css'
import styles from './Quota.module.css'
import { useQuotaOverview, useQuotaStats, useSetQuotaOverride, type QuotaKind, type QuotaMode } from './useQuota'

const KIND_LABELS: Record<QuotaKind, string> = { ask: '問答', export: '匯出', upload: '上傳' }
const KINDS: QuotaKind[] = ['ask', 'export', 'upload']
const STATS_RANGES = [7, 14, 30]
const fmtInt = (n: number) => n.toLocaleString('zh-TW')

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '操作失敗，請重試'
}

function limitText(item: QuotaItem): string {
  return item.limit == null ? '不限' : fmtInt(item.limit)
}

/** 單格：今天用了幾次／上限；超出上限的次數另外標出（影子模式＝本來會擋）。 */
function UsageCell({ item }: { item: QuotaItem }) {
  const full = item.limit != null && item.used >= item.limit
  return (
    <div className={styles.usage}>
      <span className={full ? styles.full : undefined}>{fmtInt(item.used)} / {limitText(item)}</span>
      {item.mode !== 'default' && <span className={adminStyles.badge}>{item.mode === 'unlimited' ? '不限' : '覆寫'}</span>}
      {item.over > 0 && <span className={styles.over}>超出 {fmtInt(item.over)}</span>}
    </div>
  )
}

function EnforcementCard({ data }: { data: QuotaOverview }) {
  const e = data.enforcement
  const shadow = e.mode === 'shadow'
  return (
    <section className={adminStyles.card} aria-labelledby="quota-mode-title">
      <div className={adminStyles.cardHead}>
        <h2 id="quota-mode-title" className={adminStyles.ctitle}>阻擋模式</h2>
        <span className={`${adminStyles.badge} ${shadow ? '' : adminStyles.badgeOff}`}>{shadow ? '影子模式' : '正式阻擋'}</span>
      </div>
      <div className={opsStyles.tierGrid}>
        <div className={opsStyles.tierCard} aria-label="環境變數上限">
          <div className={opsStyles.tierName}>環境變數 QUOTA_ENFORCE</div>
          <div className={opsStyles.tierCount}>{e.env_ceiling ? '開' : '關'}</div>
          <div className={opsStyles.tierHint}>能力上限；關閉時旗標怎麼設都不會擋</div>
        </div>
        <div className={opsStyles.tierCard} aria-label="功能旗標">
          <div className={opsStyles.tierName}>旗標 quota.enforce</div>
          <div className={opsStyles.tierCount}>{e.flag_enabled ? '開' : '關'}</div>
          <div className={opsStyles.tierHint}>
            {e.flag_source === 'fallback' ? '旗標讀取失敗，暫用預設（關）' : e.flag_source === 'db' ? '來自 DB 覆寫' : '程式預設'}
            {e.flag_scoped && '・只對部分角色或使用者生效'}
          </div>
        </div>
        <div className={opsStyles.tierCard} aria-label="今天超出上限">
          <div className={opsStyles.tierName}>今天超出上限（{shadow ? '本來會擋' : '已擋下'}）</div>
          <div className={opsStyles.tierCount}>{fmtInt(data.over_today.ask + data.over_today.export)}</div>
          <div className={opsStyles.tierHint}>問答 {fmtInt(data.over_today.ask)}・匯出 {fmtInt(data.over_today.export)}</div>
        </div>
      </div>
      <p className={adminStyles.hint}>
        兩道開關都開才會正式阻擋（超額回 429，台北時間午夜重置）；影子模式照常放行、只記錄。預設每人每日：問答 {fmtInt(data.defaults.ask)}、
        匯出 {fmtInt(data.defaults.export)}、上傳 {fmtInt(data.defaults.upload)}。上傳一直是正式阻擋。旗標在「功能旗標」頁調整。
      </p>
    </section>
  )
}

function StatsCard() {
  const [days, setDays] = useState(14)
  const q = useQuotaStats(days)
  return (
    <section className={adminStyles.card} aria-labelledby="quota-stats-title">
      <div className={adminStyles.cardHead}>
        <h2 id="quota-stats-title" className={adminStyles.ctitle}>每人每日用量分布</h2>
        <label className={adminStyles.field}>
          範圍
          <select value={days} onChange={e => setDays(Number(e.target.value))}>
            {STATS_RANGES.map(d => <option key={d} value={d}>最近 {d} 天</option>)}
          </select>
        </label>
      </div>
      {q.isPending ? (
        <p className={adminStyles.idle}>載入中…</p>
      ) : q.isError ? (
        <p className={adminStyles.error} role="alert">用量分布載入失敗：{messageOf(q.error)}</p>
      ) : (
        <>
          <div className={adminStyles.tableWrap}>
            <table className={adminStyles.table} aria-label="用量分布">
              <thead>
                <tr><th>類別</th><th>預設上限</th><th>P50</th><th>P95</th><th>最大</th><th>人日</th><th>人數</th><th>超額人日</th><th>超額次數</th></tr>
              </thead>
              <tbody>
                {q.data.kinds.map(k => (
                  <tr key={k.kind}>
                    <td>{KIND_LABELS[k.kind]}</td>
                    <td className={adminStyles.num}>{fmtInt(k.default_limit)}</td>
                    <td className={adminStyles.num}>{k.p50 ?? '—'}</td>
                    <td className={adminStyles.num}>{k.p95 ?? '—'}</td>
                    <td className={adminStyles.num}>{k.max ?? '—'}</td>
                    <td className={adminStyles.num}>{fmtInt(k.user_days)}</td>
                    <td className={adminStyles.num}>{fmtInt(k.users)}</td>
                    <td className={adminStyles.num}>{k.kind === 'upload' ? '—' : fmtInt(k.over_user_days)}</td>
                    <td className={adminStyles.num}>{k.kind === 'upload' ? '—' : fmtInt(k.over_events)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className={adminStyles.hint}>
            {q.data.since_day} ～ {q.data.until_day}（台北時間）。需求＝計入次數＋超出上限的嘗試；只統計有使用的人日（當天這一類至少一次）。
            觀察兩週後依 P50／P95 決定是否正式阻擋，正式啟用前不調預設值。
          </p>
        </>
      )}
    </section>
  )
}

function OverrideDialog({ user, isSuper, onClose, onSave }: {
  user: QuotaUserRow | null
  isSuper: boolean
  onClose: () => void
  onSave: (kind: QuotaKind, mode: QuotaMode, limit: number | null, reason: string) => Promise<boolean>
}) {
  const [kind, setKind] = useState<QuotaKind>('ask')
  const [mode, setMode] = useState<QuotaMode>('limit')
  const [limit, setLimit] = useState('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const current = user?.items.find(i => i.kind === kind)
  const close = () => { setKind('ask'); setMode('limit'); setLimit(''); setReason(''); setError(null); onClose() }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    let value: number | null = null
    if (mode === 'limit') {
      value = Number(limit)
      if (limit.trim() === '' || !Number.isInteger(value) || value < 0 || value > 100_000) {
        setError('每日上限必須是 0–100000 的整數'); return
      }
    }
    setError(null)
    setBusy(true)
    try {
      if (await onSave(kind, mode, value, reason)) close()
    } catch (err) {
      if (!(err instanceof ElevationCancelledError)) setError(messageOf(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open={user != null} onClose={close} title={user ? `調整「${user.username}」的配額` : ''}>
      <form className={adminStyles.dialogForm} noValidate onSubmit={e => { void submit(e) }}>
        <label className={adminStyles.field}>類別
          <select value={kind} onChange={e => setKind(e.target.value as QuotaKind)}>
            {KINDS.map(k => <option key={k} value={k}>{KIND_LABELS[k]}</option>)}
          </select>
        </label>
        {current && (
          <p className={adminStyles.hint}>
            目前：{current.mode === 'default' ? `程式預設 ${fmtInt(current.default_limit)}` : current.mode === 'unlimited' ? '不限' : `覆寫 ${limitText(current)}`}
            ・今天已用 {fmtInt(current.used)}
          </p>
        )}
        <label className={adminStyles.field}>設定
          <select value={mode} onChange={e => setMode(e.target.value as QuotaMode)}>
            <option value="limit">指定每日上限</option>
            <option value="default">恢復程式預設</option>
            {isSuper && <option value="unlimited">不限（只有 super admin）</option>}
          </select>
        </label>
        {mode === 'limit' && (
          <label className={adminStyles.field}>每日上限（0＝完全不能用）
            <input type="number" inputMode="numeric" min={0} max={100000} step={1} value={limit}
              onChange={e => setLimit(e.target.value)} required />
          </label>
        )}
        {mode !== 'default' && (
          <label className={adminStyles.field}>理由（選填，只有管理員看得到）
            <textarea value={reason} onChange={e => setReason(e.target.value)} maxLength={500} rows={2} />
          </label>
        )}
        {error && <p className={adminStyles.error} role="alert">{error}</p>}
        <div className={adminStyles.dialogActions}>
          <button type="button" className={adminStyles.action} onClick={close}>取消</button>
          <button type="submit" className={adminStyles.primary} disabled={busy}>儲存</button>
        </div>
      </form>
    </Modal>
  )
}

function UsersCard({ data, onNotice }: { data: QuotaOverview; onNotice: (msg: string, isError?: boolean) => void }) {
  const me = useMe()
  const save = useSetQuotaOverride()
  const { guard, dialog } = useElevationGate(adminApi.elevate, Boolean(me.data?.totp_enabled))
  const [editing, setEditing] = useState<QuotaUserRow | null>(null)
  const isSuper = Boolean(me.data?.is_super)

  /** 要已提升：收到 elevation_required 時彈出驗證框、驗證後自動重試；取消不算錯誤。 */
  const onSave = async (kind: QuotaKind, mode: QuotaMode, limit: number | null, reason: string) => {
    if (!editing) return false
    const name = editing.username
    const res = await guard(() => save.mutateAsync({
      userId: editing.user_id, kind, mode, daily_limit: limit, reason: reason.trim() || null,
    }))
    onNotice(res.changed ? `已更新「${name}」的${KIND_LABELS[kind]}配額` : `「${name}」的${KIND_LABELS[kind]}配額沒有變動`)
    return true
  }

  return (
    <section className={adminStyles.card} aria-labelledby="quota-users-title">
      <div className={adminStyles.cardHead}>
        <h2 id="quota-users-title" className={adminStyles.ctitle}>今天每人用量</h2>
        <span className={adminStyles.muted}>{data.day}（台北時間）</span>
      </div>
      {data.users.length === 0 ? (
        <p className={adminStyles.idle}>還沒有任何帳號。</p>
      ) : (
        <div className={adminStyles.tableWrap}>
          <table className={adminStyles.table} aria-label="每人用量">
            <thead>
              <tr><th>帳號</th><th>問答</th><th>匯出</th><th>上傳</th><th>線上 LLM</th><th>操作</th></tr>
            </thead>
            <tbody>
              {data.users.map(u => (
                <tr key={u.user_id}>
                  <td>
                    {u.username}
                    <span className={adminStyles.self}>{roleLabel(u.role)}{u.is_super ? '・super' : ''}{u.enabled ? '' : '・已停用'}</span>
                  </td>
                  {KINDS.map(k => {
                    const item = u.items.find(i => i.kind === k)
                    return <td key={k}>{item ? <UsageCell item={item} /> : '—'}</td>
                  })}
                  <td className={adminStyles.num}>
                    {fmtInt(u.llm.calls)} 次
                    <div className={adminStyles.muted}>{fmtInt(u.llm.prompt_tokens + u.llm.completion_tokens)} token</div>
                  </td>
                  <td>
                    <button type="button" className={adminStyles.action} onClick={() => setEditing(u)}
                      disabled={u.is_super && !isSuper} title={u.is_super && !isSuper ? '只有 super admin 能調整 super admin 的配額' : undefined}>
                      調整
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className={adminStyles.hint}>只有次數、沒有主題。「超出」是超過上限的次數：影子模式下照常放行（本來會擋），正式阻擋時是被擋下的次數。線上 LLM 是今天問答與抽查的呼叫（只記 metadata）。</p>
      <OverrideDialog user={editing} isSuper={isSuper} onClose={() => setEditing(null)} onSave={onSave} />
      {dialog}
    </section>
  )
}

function QuotaContent() {
  const q = useQuotaOverview()
  const [notice, setNotice] = useState<{ msg: string; isError: boolean } | null>(null)
  return (
    <div className={adminStyles.page}>
      <div className={adminStyles.inner}>
        <AdminHeader title="配額" subtitle="每人每日的問答、匯出與上傳次數與個人覆寫。正式阻擋前先以影子模式觀察。" />
        {notice && (
          <p className={notice.isError ? adminStyles.error : adminStyles.ok} role={notice.isError ? 'alert' : 'status'}>
            {notice.msg}
          </p>
        )}
        {q.isPending ? (
          <p className={adminStyles.idle}>載入中…</p>
        ) : q.isError ? (
          <p className={adminStyles.error} role="alert">配額載入失敗：{messageOf(q.error)}</p>
        ) : (
          <>
            <EnforcementCard data={q.data} />
            <UsersCard data={q.data} onNotice={(msg, isError = false) => setNotice({ msg, isError })} />
          </>
        )}
        <StatsCard />
      </div>
    </div>
  )
}

/** 配額（/app/admin/quota；Admin v2 Quota lane）。後端 /api/admin/quota*（accounts.manage）。 */
export default function AdminQuotaPage() {
  return (
    <RequireAdmin>
      <RequireScope scope="accounts.manage" title="配額">
        <QuotaContent />
      </RequireScope>
    </RequireAdmin>
  )
}
