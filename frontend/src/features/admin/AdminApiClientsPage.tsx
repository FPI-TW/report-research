import { useState, type FormEvent } from 'react'
import { ConfirmDialog } from '../../components/primitives/ConfirmDialog'
import { Modal } from '../../components/primitives/Modal'
import { RequireAdmin } from '../../components/shell/RequireAdmin'
import { CopyButton } from '../../components/animate-ui/components/buttons/copy'
import { adminApi, type ApiClientEntitlements } from '../../lib/generated/adminApi'
import { MARKET_ORDER, marketLabel } from '../../lib/meta'
import { useMe } from '../../lib/useMe'
import { ElevationCancelledError, useElevationGate } from '../account/useElevationGate'
import { AdminHeader } from './AdminHeader'
import { fmtDateTime } from './auditLabels'
import { RequireScope } from './RequireScope'
import {
  useApiClientActions, useApiClients, type AdminApiClient, type ApiClientPatch, type ApiClientScope,
} from './useAdminApiClients'
import styles from './Admin.module.css'
import local from './ApiClients.module.css'

/** 錯誤物件 → 給人看的訊息。後端 400／404／409 的 detail 由 requestJSON 放進 message，原樣顯示。 */
function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '操作失敗，請重試'
}

const SCOPE_LABELS: Record<ApiClientScope, string> = {
  search: '語意檢索（search）',
  'report.file': '研報原檔連結（report.file）',
}
const SCOPE_ORDER = Object.keys(SCOPE_LABELS) as ApiClientScope[]

// 選填維度：留空＝不限。值是語料裡的原字串（券商名、報告類型、商品類型代碼），逐字比對。
const OPTIONAL_DIMS = [
  { key: 'source', label: '券商', example: '元大、凱基' },
  { key: 'report_type', label: '報告類型', example: '個股報告、產業報告' },
  { key: 'instrument_type', label: '商品類型', example: 'equity、etf' },
] as const
type OptionalDim = (typeof OPTIONAL_DIMS)[number]['key']

// 與後端 app/services/api_clients.py 的範圍相同；這裡只是提早提示，後端仍會再驗一次。
const RATE_RANGE = [1, 6000] as const
const QUOTA_RANGE = [1, 1_000_000] as const
const NAME_MAX = 100
const NOTE_MAX = 1000

/** 「元大、凱基, 永豐」→ ['元大', '凱基', '永豐']；頓號、逗號、換行都算分隔，空白值丟掉。 */
function splitValues(text: string): string[] {
  return [...new Set(text.split(/[、,，\n]/).map(s => s.trim()).filter(Boolean))]
}

type FormState = {
  name: string
  scopes: ApiClientScope[]
  rate: string
  quota: string
  note: string
  markets: string[]
  dims: Record<OptionalDim, string>
}

function initialForm(c: AdminApiClient | null): FormState {
  const e = c?.entitlements
  return {
    name: c?.name ?? '',
    scopes: c ? [...c.scopes] : ['search'],
    rate: String(c?.rate_limit_per_min ?? 60),
    quota: String(c?.daily_quota ?? 1000),
    note: c?.note ?? '',
    markets: e ? [...e.market] : [],
    dims: {
      source: (e?.source ?? []).join('、'),
      report_type: (e?.report_type ?? []).join('、'),
      instrument_type: (e?.instrument_type ?? []).join('、'),
    },
  }
}

function entitlementsOf(f: FormState): ApiClientEntitlements {
  const out: ApiClientEntitlements = { market: MARKET_ORDER.filter(m => f.markets.includes(m)) }
  for (const { key } of OPTIONAL_DIMS) {
    const values = splitValues(f.dims[key])
    if (values.length) out[key] = values
  }
  return out
}

const sameList = (a: readonly string[] | null | undefined, b: readonly string[] | null | undefined) =>
  [...(a ?? [])].sort().join('\n') === [...(b ?? [])].sort().join('\n')

function sameEntitlements(a: ApiClientEntitlements, b: ApiClientEntitlements): boolean {
  return sameList(a.market, b.market) && OPTIONAL_DIMS.every(({ key }) => sameList(a[key], b[key]))
}

/** 表格裡的授權範圍摘要：市場一定有；其他維度沒設定就不提（＝不限）。 */
function entitlementSummary(e: ApiClientEntitlements): string {
  const parts = [e.market.map(marketLabel).join('、')]
  for (const { key, label } of OPTIONAL_DIMS) {
    const values = e[key]
    if (values?.length) parts.push(`${label}：${values.join('、')}`)
  }
  return parts.join('；')
}

type Submit = { settings: ApiClientPatch | null; entitlements: ApiClientEntitlements | null; form: FormState }

/** 建立與編輯共用的表單。編輯時名稱不可改（後端沒有改名），只送真的有變的欄位。 */
function ClientForm({ client, busy, onCancel, onSubmit }: {
  client: AdminApiClient | null
  busy: boolean
  onCancel: () => void
  onSubmit: (s: Submit) => Promise<void>
}) {
  const [f, setF] = useState<FormState>(() => initialForm(client))
  const [error, setError] = useState<string | null>(null)
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setF(cur => ({ ...cur, [k]: v }))
  const toggle = <T extends string>(list: T[], v: T) => (list.includes(v) ? list.filter(x => x !== v) : [...list, v])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const rate = Number(f.rate)
    const quota = Number(f.quota)
    if (!client && !f.name.trim()) { setError('請填寫名稱'); return }
    if (!Number.isInteger(rate) || rate < RATE_RANGE[0] || rate > RATE_RANGE[1]) {
      setError(`每分鐘限流需為 ${RATE_RANGE[0]}–${RATE_RANGE[1]} 的整數`); return
    }
    if (!Number.isInteger(quota) || quota < QUOTA_RANGE[0] || quota > QUOTA_RANGE[1]) {
      setError(`每日額度需為 ${QUOTA_RANGE[0]}–${QUOTA_RANGE[1].toLocaleString()} 的整數`); return
    }
    if (f.markets.length === 0) { setError('至少要允許一個市場'); return }
    setError(null)
    const ents = entitlementsOf(f)
    let settings: ApiClientPatch | null = null
    let entitlements: ApiClientEntitlements | null = ents
    if (client) {
      const patch: ApiClientPatch = {}
      if (!sameList(f.scopes, client.scopes)) patch.scopes = SCOPE_ORDER.filter(s => f.scopes.includes(s))
      if (rate !== client.rate_limit_per_min) patch.rate_limit_per_min = rate
      if (quota !== client.daily_quota) patch.daily_quota = quota
      if (f.note.trim() !== (client.note ?? '')) patch.note = f.note.trim()
      settings = Object.keys(patch).length ? patch : null
      entitlements = sameEntitlements(ents, client.entitlements) ? null : ents
      if (!settings && !entitlements) { onCancel(); return }
    }
    try {
      await onSubmit({ settings, entitlements, form: { ...f, rate: String(rate), quota: String(quota) } })
    } catch (err) {
      if (!(err instanceof ElevationCancelledError)) setError(messageOf(err))
    }
  }

  return (
    <form className={styles.dialogForm} onSubmit={submit} noValidate>
      <label className={styles.field}>名稱{client ? '（不可修改）' : ''}
        <input value={f.name} onChange={e => set('name', e.target.value)} maxLength={NAME_MAX} autoComplete="off"
          readOnly={client != null} disabled={client != null} />
      </label>
      <fieldset className={local.fieldset}>
        <legend className={local.legend}>可呼叫的端點</legend>
        {SCOPE_ORDER.map(s => (
          <label key={s} className={styles.check}>
            <input type="checkbox" checked={f.scopes.includes(s)} onChange={() => set('scopes', toggle(f.scopes, s))} />
            {SCOPE_LABELS[s]}
          </label>
        ))}
      </fieldset>
      <div className={styles.form}>
        <label className={styles.field}>每分鐘限流
          <input type="number" inputMode="numeric" min={RATE_RANGE[0]} max={RATE_RANGE[1]} value={f.rate}
            onChange={e => set('rate', e.target.value)} />
        </label>
        <label className={styles.field}>每日額度
          <input type="number" inputMode="numeric" min={QUOTA_RANGE[0]} max={QUOTA_RANGE[1]} value={f.quota}
            onChange={e => set('quota', e.target.value)} />
        </label>
      </div>
      <fieldset className={local.fieldset}>
        <legend className={local.legend}>授權市場（必選，至少一個）</legend>
        <div className={local.checkGrid}>
          {MARKET_ORDER.map(m => (
            <label key={m} className={styles.check}>
              <input type="checkbox" checked={f.markets.includes(m)} onChange={() => set('markets', toggle(f.markets, m))} />
              {marketLabel(m)}（{m}）
            </label>
          ))}
        </div>
      </fieldset>
      {OPTIONAL_DIMS.map(({ key, label, example }) => (
        <label key={key} className={styles.field}>{label}（留空＝不限；多個以頓號或逗號分隔，例如 {example}）
          <input value={f.dims[key]} autoComplete="off"
            onChange={e => set('dims', { ...f.dims, [key]: e.target.value })} />
        </label>
      ))}
      <label className={styles.field}>備註（選填，最多 {NOTE_MAX} 字）
        <textarea value={f.note} onChange={e => set('note', e.target.value)} rows={2} maxLength={NOTE_MAX} />
      </label>
      {!client && <p className={styles.hint}>建立後會產生一把 API 金鑰，只顯示一次；建立需要重新驗證身分。</p>}
      {error && <p className={styles.error} role="alert">{error}</p>}
      <div className={styles.dialogActions}>
        <button type="button" className={styles.action} onClick={onCancel}>取消</button>
        <button type="submit" className={styles.primary} disabled={busy}>{client ? '儲存' : '建立'}</button>
      </div>
    </form>
  )
}

/**
 * 原始金鑰的一次性對話框。金鑰只活在這個元件的 props（父層 state）裡：關閉後父層把它清成 null，
 * 前端沒有任何地方能再取得——後端只存 hash，遺失只能輪替。
 */
function KeyDialog({ shown, onClose }: { shown: { name: string; key: string } | null; onClose: () => void }) {
  return (
    <Modal open={shown != null} onClose={onClose} title={shown ? `「${shown.name}」的 API 金鑰` : ''} className={local.keyModal}>
      <div className={styles.dialogForm}>
        <p className={local.keyWarn} role="note">
          請立即複製並妥善保存，關閉後將<strong>無法再次查看</strong>。
        </p>
        {/* 複製失敗時 CopyButton 不翻成勾勾（勾勾是「已複製」的承諾）；金鑰框可整段選取，手動複製仍可行。 */}
        <div className={local.keyField}>
          <code className={local.keyBox} aria-label="API 金鑰">{shown?.key}</code>
          <CopyButton
            content={shown?.key ?? ''}
            variant="ghost"
            size="sm"
            className={local.keyCopy}
            aria-label="複製金鑰"
            title="複製金鑰"
          />
        </div>
        <div className={styles.dialogActions}>
          <button type="button" className={styles.primary} onClick={onClose}>我已妥善保存，關閉</button>
        </div>
      </div>
    </Modal>
  )
}

type Pending = { kind: 'disable' | 'rotate'; client: AdminApiClient }

const CONFIRM_TEXT: Record<Pending['kind'], (c: AdminApiClient) => { title: string; body: string; label: string }> = {
  disable: c => ({
    title: `停用「${c.name}」？`,
    body: '停用後立即生效：這個 API 用戶端的下一個請求就會被拒絕。設定與金鑰都保留，可以隨時重新啟用。',
    label: '停用',
  }),
  rotate: c => ({
    title: `輪替「${c.name}」的金鑰？`,
    body: '會產生一把新金鑰並只顯示一次；舊金鑰在下一個請求就失效，對方必須改用新金鑰。需要重新驗證身分。',
    label: '輪替金鑰',
  }),
}

function ApiClients() {
  const clients = useApiClients()
  const me = useMe()
  const { create, update, entitlements, rotate } = useApiClientActions()
  const { guard, dialog, open: elevating } = useElevationGate(adminApi.elevate, Boolean(me.data?.totp_enabled))
  const [notice, setNotice] = useState<{ msg: string; isError: boolean } | null>(null)
  const [editing, setEditing] = useState<{ client: AdminApiClient | null } | null>(null)
  const [pending, setPending] = useState<Pending | null>(null)
  const [shownKey, setShownKey] = useState<{ name: string; key: string } | null>(null)
  const onNotice = (msg: string, isError = false) => setNotice({ msg, isError })
  const busy = create.isPending || update.isPending || entitlements.isPending || rotate.isPending
  // 驗證框開著時外層表單不跟著 Escape 關閉（兩個 Modal 都會收到 keydown）。
  const closeEditor = () => { if (!elevating) setEditing(null) }

  const save = async ({ settings, entitlements: ents, form }: Submit) => {
    const target = editing?.client ?? null
    if (!target) {
      const res = await guard(() => create.mutateAsync({
        name: form.name.trim(), scopes: SCOPE_ORDER.filter(s => form.scopes.includes(s)),
        rate_limit_per_min: Number(form.rate), daily_quota: Number(form.quota),
        entitlements: ents!, note: form.note.trim() || null,
      }))
      create.reset()
      setEditing(null)
      setShownKey({ name: res.name, key: res.api_key })
      onNotice(`已建立 API 用戶端「${res.name}」`)
      return
    }
    if (settings) await update.mutateAsync({ id: target.id, ...settings })
    if (ents) await entitlements.mutateAsync({ id: target.id, ...ents })
    setEditing(null)
    onNotice(`已更新「${target.name}」`)
  }

  const run = async (p: Pending) => {
    setPending(null)
    try {
      if (p.kind === 'disable') {
        await update.mutateAsync({ id: p.client.id, enabled: false })
        onNotice(`已停用「${p.client.name}」`)
      } else {
        const res = await guard(() => rotate.mutateAsync(p.client.id))
        rotate.reset()
        setShownKey({ name: res.name, key: res.api_key })
        onNotice(`已輪替「${res.name}」的金鑰，舊金鑰已失效`)
      }
    } catch (err) {
      if (!(err instanceof ElevationCancelledError)) onNotice(messageOf(err), true)
    }
  }
  const enable = (c: AdminApiClient) => update.mutate({ id: c.id, enabled: true }, {
    onSuccess: () => onNotice(`已啟用「${c.name}」`), onError: err => onNotice(messageOf(err), true),
  })

  const confirm = pending ? CONFIRM_TEXT[pending.kind](pending.client) : null

  return (
    <div className={styles.page}>
      <div className={styles.inner}>
        <AdminHeader title="API 用戶端" subtitle="管理對外 API（/external/v1/*）的用戶端：金鑰、可呼叫的端點、限流、每日額度與授權範圍；每一筆操作都會留在「操作紀錄」。" />
        {notice && (
          <p className={notice.isError ? styles.error : styles.ok} role={notice.isError ? 'alert' : 'status'}>{notice.msg}</p>
        )}
        <section className={styles.card} aria-labelledby="api-clients-title">
          <div className={styles.cardHead}>
            <h2 id="api-clients-title" className={styles.ctitle}>API 用戶端清單</h2>
            <button type="button" className={styles.primary} onClick={() => setEditing({ client: null })}>
              建立 API 用戶端
            </button>
          </div>
          {clients.isPending ? (
            <p className={styles.idle}>載入中…</p>
          ) : clients.isError ? (
            <p className={styles.error} role="alert">API 用戶端清單載入失敗：{messageOf(clients.error)}</p>
          ) : clients.data.length === 0 ? (
            <p className={styles.idle}>還沒有任何 API 用戶端</p>
          ) : (
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>名稱</th><th>金鑰前綴</th><th>狀態</th><th>端點</th><th>限流／額度</th><th>授權範圍</th>
                    <th>最後使用</th><th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {clients.data.map(c => (
                    <tr key={c.id}>
                      <td className={styles.wrapCell}>
                        {c.name}
                        {c.note && <div className={styles.reason}>{c.note}</div>}
                      </td>
                      <td className={styles.num}><code>rmk_{c.key_prefix}_…</code></td>
                      <td>
                        <span className={`${styles.badge} ${c.enabled ? '' : styles.badgeOff}`}>{c.enabled ? '啟用' : '停用'}</span>
                      </td>
                      <td>{c.scopes.length ? c.scopes.join('、') : <span className={styles.muted}>（無）</span>}</td>
                      <td className={styles.num}>
                        <div>{c.rate_limit_per_min.toLocaleString()}／分</div>
                        <div className={styles.muted}>{c.daily_quota.toLocaleString()}／日</div>
                      </td>
                      <td className={styles.wrapCell}>{entitlementSummary(c.entitlements)}</td>
                      <td className={styles.num}>{fmtDateTime(c.last_used_at)}</td>
                      <td className={styles.actionsCell}>
                        <div className={styles.actions}>
                          <button type="button" className={styles.action} disabled={busy}
                            onClick={() => setEditing({ client: c })}>編輯</button>
                          {c.enabled ? (
                            <button type="button" className={`${styles.action} ${styles.danger}`} disabled={busy}
                              onClick={() => setPending({ kind: 'disable', client: c })}>停用</button>
                          ) : (
                            <button type="button" className={styles.action} disabled={busy} onClick={() => enable(c)}>啟用</button>
                          )}
                          <button type="button" className={styles.action} disabled={busy}
                            onClick={() => setPending({ kind: 'rotate', client: c })}>輪替金鑰</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className={styles.hint}>
            金鑰只在建立與輪替時顯示一次，系統只保存雜湊值。停用、輪替與設定變更都在對方的下一個請求生效。
            授權範圍：市場必選；券商、報告類型、商品類型留空＝不限。建立與輪替需要重新驗證身分。
          </p>
        </section>
        <Modal open={editing != null} onClose={closeEditor}
          title={editing?.client ? `編輯「${editing.client.name}」` : '建立 API 用戶端'}>
          {/* key：換一個用戶端就重建表單，初值直接取自該用戶端（不在 effect 裡同步 state） */}
          {editing && (
            <ClientForm key={editing.client?.id ?? 'new'} client={editing.client} busy={busy}
              onCancel={closeEditor} onSubmit={save} />
          )}
        </Modal>
        <ConfirmDialog
          open={confirm != null}
          title={confirm?.title ?? ''}
          body={confirm?.body ?? ''}
          confirmLabel={confirm?.label ?? '確認'}
          onConfirm={() => { if (pending) void run(pending) }}
          onCancel={() => setPending(null)}
        />
        <KeyDialog shown={shownKey} onClose={() => setShownKey(null)} />
        {dialog}
      </div>
    </div>
  )
}

export default function AdminApiClientsPage() {
  return (
    <RequireAdmin>
      <RequireScope scope="api_clients.manage" title="API 用戶端管理"><ApiClients /></RequireScope>
    </RequireAdmin>
  )
}
