import { useState } from 'react'
import { Link } from 'react-router'
import {
  Card, Chip, ConsolePage, PageHeader, RowActions, Segmented, StatTile, toast, useElevate,
} from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { RequireAdmin } from '../../../components/shell/RequireAdmin'
import type { QuotaItem, QuotaOverview } from '../../../lib/generated/adminApi'
import { useHasScope, useMe } from '../../../lib/useMe'
import { roleLabel } from '../auditLabels'
import { RequireScope } from '../RequireScope'
import { KIND_LABELS, KINDS, fmtInt, limitText, messageOf } from './quotaLabels'
import { QuotaLimitDialog } from './QuotaLimitDialog'
import type { QuotaChange } from './quotaPlan'
import { useQuotaOverview, useQuotaStats, useSetQuotaOverride } from './useQuota'
import styles from './Quota.module.css'

const STATS_RANGES = ['7', '14', '30'] as const
type StatsRange = (typeof STATS_RANGES)[number]
const SUPER_ONLY_HINT = '只有 super admin 能調整 super admin 的配額'

/**
 * 單格：今天用了幾次／上限。超出上限的次數另外標出：影子模式下那些請求照常放行（本來會擋），
 * 正式阻擋時是被擋下的次數；上傳一直是正式阻擋。
 */
function UsageCell({ item, shadow }: { item: QuotaItem; shadow: boolean }) {
  const full = item.limit != null && item.used >= item.limit
  return (
    <>
      <div className={styles.usage}>
        <span className={c.num} data-tone={item.over > 0 ? 'bad' : full ? 'warn' : undefined}>
          {fmtInt(item.used)}／{limitText(item)}
        </span>
        {item.mode !== 'default' && <Chip tone="gold">{item.mode === 'unlimited' ? '不限' : '覆寫'}</Chip>}
      </div>
      {item.over > 0 && (
        <div className={styles.over}>
          超出 {fmtInt(item.over)}（{shadow && item.kind !== 'upload' ? '影子模式照常放行' : '已擋下'}）
        </div>
      )}
    </>
  )
}

function EnforcementTiles({ data }: { data: QuotaOverview }) {
  const e = data.enforcement
  const shadow = e.mode === 'shadow'
  const over = data.over_today.ask + data.over_today.export
  const flagSource = e.flag_source === 'fallback' ? '旗標讀取失敗，暫用預設（關）' : e.flag_source === 'db' ? '來自 DB 覆寫' : '程式預設'
  const flagSub = `${flagSource}${e.flag_scoped ? '・只對部分角色或使用者生效' : ''}`
  return (
    <div className={c.tiles}>
      <StatTile label="阻擋模式" value={shadow ? '影子模式' : '正式阻擋'}
        sub={shadow ? '照常放行、只記錄' : '超額回 429'} />
      <StatTile label="環境變數 QUOTA_ENFORCE" value={e.env_ceiling ? '開' : '關'}
        sub={<span title="能力上限；關閉時旗標怎麼設都不會擋">能力上限；關閉時旗標怎麼設都不會擋</span>} />
      <StatTile label="旗標 quota.enforce" value={e.flag_enabled ? '開' : '關'}
        tone={e.flag_source === 'fallback' ? 'warn' : undefined} lamp={e.flag_source === 'fallback' ? 'warn' : undefined}
        sub={<span title={flagSub}>{flagSub}</span>} />
      <StatTile label={`今天超出上限（${shadow ? '本來會擋' : '已擋下'}）`} value={fmtInt(over)} unit="次"
        sub={`問答 ${fmtInt(data.over_today.ask)}・匯出 ${fmtInt(data.over_today.export)}`} />
      <StatTile label="預設每日上限" value={fmtInt(data.defaults.ask)} unit="問答"
        sub={`匯出 ${fmtInt(data.defaults.export)}・上傳 ${fmtInt(data.defaults.upload)}`} />
    </div>
  )
}

function StatsCard() {
  const [range, setRange] = useState<StatsRange>('14')
  const q = useQuotaStats(Number(range))
  return (
    <Card title="每人每日用量分布" labelledBy="quota-stats-title"
      right={<Segmented label="範圍" value={range} onChange={setRange} options={STATS_RANGES.map(d => ({ value: d, label: `${d} 天` }))} />}>
      {q.isPending ? (
        <p className={c.empty}>載入中…</p>
      ) : q.isError ? (
        <div className={c.cardBody}><p className={c.error} role="alert">用量分布載入失敗：{messageOf(q.error)}</p></div>
      ) : (
        <>
          <div className={c.tableWrap}>
            <table className={c.table} aria-label="用量分布">
              <thead>
                <tr>
                  <th>類別</th><th className={c.tdRight}>預設上限</th><th className={c.tdRight}>P50</th><th className={c.tdRight}>P95</th>
                  <th className={c.tdRight}>最大</th><th className={c.tdRight}>人日</th><th className={c.tdRight}>人數</th>
                  <th className={c.tdRight}>超額人日</th><th className={c.tdRight}>超額次數</th>
                </tr>
              </thead>
              <tbody>
                {q.data.kinds.map(k => (
                  <tr key={k.kind}>
                    <td className={c.t1}>{KIND_LABELS[k.kind]}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{fmtInt(k.default_limit)}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{k.p50 ?? '—'}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{k.p95 ?? '—'}</td>
                    <td className={`${c.num} ${c.tdRight} ${styles.stat}`} data-tone={k.max != null && k.max > k.default_limit ? 'warn' : undefined}>
                      {k.max ?? '—'}
                    </td>
                    <td className={`${c.num} ${c.tdRight}`}>{fmtInt(k.user_days)}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{fmtInt(k.users)}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{k.kind === 'upload' ? '—' : fmtInt(k.over_user_days)}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{k.kind === 'upload' ? '—' : fmtInt(k.over_events)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className={`${c.cardFoot} ${styles.foot}`}>
            <span>{q.data.since_day} ～ {q.data.until_day}（台北時間）</span>
            <span>需求＝計入次數＋超出上限的嘗試；只統計有使用的人日</span>
          </div>
        </>
      )}
    </Card>
  )
}

function UsersCard({ data }: { data: QuotaOverview }) {
  const me = useMe()
  const save = useSetQuotaOverride()
  const { elevated, dialog } = useElevate()
  const [editingId, setEditingId] = useState<string | null>(null)
  const isSuper = Boolean(me.data?.is_super)
  const shadow = data.enforcement.mode === 'shadow'
  // 表單比對的是清單裡最新的那一列（存了一部分之後重抓，剩下的才是「還沒改」的）。
  const editing = data.users.find(u => u.user_id === editingId) ?? null

  /** 調整配額要已提升：`useElevate` 先確認身分再送；一個類別一筆 PUT，依序送出。 */
  const onSave = async (changes: QuotaChange[]) => {
    if (!editing) return
    const { user_id: userId, username } = editing
    const results = await elevated('調整配額', async () => {
      const out = []
      for (const { kind, ...body } of changes) out.push(await save.mutateAsync({ userId, kind, ...body }))
      return out
    })
    toast(results.some(r => r.changed) ? `已調整「${username}」的上限` : `「${username}」的上限沒有變動`)
    setEditingId(null)
  }

  return (
    <Card title="今天每人用量" labelledBy="quota-users-title" right={<span className={c.mono}>{data.day}（台北時間）</span>}>
      {data.users.length === 0 ? (
        <p className={c.empty}>還沒有任何帳號</p>
      ) : (
        <div className={c.tableWrap}>
          <table className={c.table} aria-label="每人用量">
            <thead>
              <tr><th>帳號</th><th>問答</th><th>匯出</th><th>上傳</th><th>線上 LLM</th><th className={c.tdActions}>操作</th></tr>
            </thead>
            <tbody>
              {data.users.map(u => {
                const locked = u.is_super && !isSuper
                return (
                  <tr key={u.user_id}>
                    <td>
                      <div className={styles.account}>
                        <span className={c.t1}>{u.username}</span>
                        {u.is_super && <Chip tone="gold">super</Chip>}
                        {!u.enabled && <Chip tone="idle">已停用</Chip>}
                      </div>
                      <div className={c.sub}>{roleLabel(u.role)}</div>
                    </td>
                    {KINDS.map(k => {
                      const item = u.items.find(i => i.kind === k)
                      return <td key={k}>{item ? <UsageCell item={item} shadow={shadow} /> : <span className={c.muted}>—</span>}</td>
                    })}
                    <td>
                      <span className={c.num}>{fmtInt(u.llm.calls)} 次</span>
                      <div className={`${c.sub} ${c.num}`}>{fmtInt(u.llm.prompt_tokens + u.llm.completion_tokens)} token</div>
                    </td>
                    <td className={c.tdActions}>
                      <RowActions primary={{
                        label: '調整上限', onClick: () => setEditingId(u.user_id),
                        disabled: locked, hint: locked ? SUPER_ONLY_HINT : undefined,
                      }} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className={`${c.cardFoot} ${styles.foot}`}>
        <span>只有次數、沒有主題</span>
        <span>線上 LLM 是今天問答與抽查的呼叫（只記 metadata）</span>
      </div>
      {editing && (
        <QuotaLimitDialog key={editing.user_id} user={editing} isSuper={isSuper}
          onClose={() => setEditingId(null)} onSave={onSave} />
      )}
      {dialog}
    </Card>
  )
}

function QuotaContent() {
  const q = useQuotaOverview()
  const canSeeFlags = useHasScope('ops.read')
  const mode = q.data?.enforcement.mode
  const summary = mode === 'shadow'
    ? '每人每日問答、匯出與上傳上限；目前是影子模式，只計數、記錄本來會擋的請求。'
    : mode === 'enforce'
      ? '每人每日問答、匯出與上傳上限；目前正式阻擋，超額的請求回 429。'
      : '每人每日問答、匯出與上傳上限，以及個人覆寫。'
  return (
    <ConsolePage>
      <PageHeader summary={summary}
        actions={canSeeFlags && <Link className={c.btn} to="/admin/flags">前往功能旗標</Link>}
        help={<>
          每日額度以台北時間日曆日計，午夜重置。環境變數 <code>QUOTA_ENFORCE</code>（能力上限）與功能旗標 <code>quota.enforce</code> 兩道都開才會正式阻擋（超額回 429）；
          只開一邊時是影子模式：照常放行、只記錄本來會擋的請求。上傳一直是正式阻擋。旗標在「功能旗標」頁調整。
          「超出」是超過上限的次數：影子模式下照常放行，正式阻擋時是被擋下的次數。
          觀察兩週後依 P50／P95 決定是否正式阻擋，正式啟用前不調預設值。調整個人上限需要重新驗證身分，每一筆都會留在「操作紀錄」。
        </>} />
      {q.isPending ? (
        <p className={c.empty}>載入中…</p>
      ) : q.isError ? (
        <p className={c.error} role="alert">配額載入失敗：{messageOf(q.error)}</p>
      ) : (
        <>
          <EnforcementTiles data={q.data} />
          <UsersCard data={q.data} />
        </>
      )}
      <StatsCard />
    </ConsolePage>
  )
}

/** 配額（/admin/quota）。後端 /api/admin/quota*（accounts.manage）。 */
export default function AdminQuotaPage() {
  return (
    <RequireAdmin>
      <RequireScope scope="accounts.manage" title="配額">
        <QuotaContent />
      </RequireScope>
    </RequireAdmin>
  )
}
