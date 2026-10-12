import { useState } from 'react'
import { Field, FormDialog, isElevationCancelled } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type { ApiClientEntitlements, CreateApiClientRequest } from '../../../lib/generated/adminApi'
import type { AdminApiClient, ApiClientPatch, ApiClientScope } from '../useAdminApiClients'
import {
  NAME_MAX, SCOPE_ORDER, dimTextOf, entitlementsOf, limitsError, messageOf, sameEntitlements, sameList, type DimText,
} from './clientFields'
import { DimFields, LimitFields, MarketChecks, NoteField, ScopeChecks } from './ClientFormParts'

/** 表單送出的共同流程：忙碌狀態、後端錯誤顯示在表單裡；重新驗證被取消不算錯誤。 */
function useSubmit() {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const run = async (action: () => Promise<void>) => {
    setError(null)
    setBusy(true)
    try {
      await action()
    } catch (err) {
      if (!isElevationCancelled(err)) setError(messageOf(err))
    } finally {
      setBusy(false)
    }
  }
  return { error, setError, busy, run }
}

/** 建立用戶端。成功後由呼叫端關閉並顯示只出現一次的金鑰。 */
export function CreateClientDialog({ onClose, onCreate }: {
  onClose: () => void
  onCreate: (body: CreateApiClientRequest) => Promise<void>
}) {
  const [name, setName] = useState('')
  const [scopes, setScopes] = useState<ApiClientScope[]>(['search'])
  const [rate, setRate] = useState('60')
  const [quota, setQuota] = useState('1000')
  const [markets, setMarkets] = useState<string[]>([])
  const [dims, setDims] = useState<DimText>(() => dimTextOf(undefined))
  const [note, setNote] = useState('')
  const { error, setError, busy, run } = useSubmit()

  const submit = () => {
    if (!name.trim()) { setError('請填寫名稱'); return }
    const invalid = limitsError(Number(rate), Number(quota))
    if (invalid) { setError(invalid); return }
    if (markets.length === 0) { setError('至少要允許一個市場'); return }
    void run(() => onCreate({
      name: name.trim(), scopes: SCOPE_ORDER.filter(s => scopes.includes(s)),
      rate_limit_per_min: Number(rate), daily_quota: Number(quota),
      entitlements: entitlementsOf(markets, dims), note: note.trim() || null,
    }))
  }

  return (
    <FormDialog open title="建立 API 用戶端" body="建立後會產生一把金鑰，只顯示這一次。建立需要重新驗證身分。"
      submitLabel="建立" busy={busy} error={error} onSubmit={submit} onClose={onClose}>
      <Field label="名稱" counter={`${name.length}／${NAME_MAX}`}>
        <input className={c.input} value={name} maxLength={NAME_MAX} autoComplete="off" placeholder="例如：partner-portal"
          onChange={e => setName(e.target.value)} />
      </Field>
      <ScopeChecks value={scopes} onChange={setScopes} />
      <LimitFields rate={rate} quota={quota} onRate={setRate} onQuota={setQuota} />
      <MarketChecks value={markets} onChange={setMarkets} />
      <DimFields value={dims} onChange={setDims} />
      <NoteField value={note} onChange={setNote} />
    </FormDialog>
  )
}

/** 調整限流與額度：只送真的有變的欄位。 */
export function LimitsDialog({ client, onClose, onSave }: {
  client: AdminApiClient
  onClose: () => void
  onSave: (patch: ApiClientPatch) => Promise<void>
}) {
  const [rate, setRate] = useState(String(client.rate_limit_per_min))
  const [quota, setQuota] = useState(String(client.daily_quota))
  const { error, setError, busy, run } = useSubmit()
  const unchanged = Number(rate) === client.rate_limit_per_min && Number(quota) === client.daily_quota

  const submit = () => {
    const invalid = limitsError(Number(rate), Number(quota))
    if (invalid) { setError(invalid); return }
    const patch: ApiClientPatch = {}
    if (Number(rate) !== client.rate_limit_per_min) patch.rate_limit_per_min = Number(rate)
    if (Number(quota) !== client.daily_quota) patch.daily_quota = Number(quota)
    void run(() => onSave(patch))
  }

  return (
    <FormDialog open title={`調整「${client.name}」的限流與額度`} body="下一個請求就生效。每日額度以台北時間日曆日計。"
      submitLabel="儲存" submitDisabled={unchanged} busy={busy} error={error} onSubmit={submit} onClose={onClose}>
      <LimitFields rate={rate} quota={quota} onRate={setRate} onQuota={setQuota} />
    </FormDialog>
  )
}

/**
 * 變更授權範圍：可呼叫的端點＋市場＋選填維度。端點有變才 PATCH，授權清單有變才 PUT 完整清單
 * （後端的 entitlements 是整份取代）。
 */
export function ScopeDialog({ client, onClose, onSave }: {
  client: AdminApiClient
  onClose: () => void
  onSave: (change: { scopes: ApiClientScope[] | null; entitlements: ApiClientEntitlements | null }) => Promise<void>
}) {
  const [scopes, setScopes] = useState<ApiClientScope[]>(() => [...client.scopes])
  const [markets, setMarkets] = useState<string[]>(() => [...client.entitlements.market])
  const [dims, setDims] = useState<DimText>(() => dimTextOf(client.entitlements))
  const { error, setError, busy, run } = useSubmit()
  const next = entitlementsOf(markets, dims)
  const scopesChanged = !sameList(scopes, client.scopes)
  const entitlementsChanged = !sameEntitlements(next, client.entitlements)

  const submit = () => {
    if (markets.length === 0) { setError('至少要允許一個市場'); return }
    void run(() => onSave({
      scopes: scopesChanged ? SCOPE_ORDER.filter(s => scopes.includes(s)) : null,
      entitlements: entitlementsChanged ? next : null,
    }))
  }

  return (
    <FormDialog open title={`變更「${client.name}」的授權範圍`} body="市場必填；券商、報告類型、商品類型不設定就是不限。"
      submitLabel="儲存" submitDisabled={!scopesChanged && !entitlementsChanged} busy={busy} error={error}
      onSubmit={submit} onClose={onClose}>
      <ScopeChecks value={scopes} onChange={setScopes} />
      <MarketChecks value={markets} onChange={setMarkets} />
      <DimFields value={dims} onChange={setDims} />
    </FormDialog>
  )
}

/** 編輯備註：傳空字串＝清除備註。 */
export function NoteDialog({ client, onClose, onSave }: {
  client: AdminApiClient
  onClose: () => void
  onSave: (note: string) => Promise<void>
}) {
  const [note, setNote] = useState(client.note ?? '')
  const { error, busy, run } = useSubmit()
  return (
    <FormDialog open title={`編輯「${client.name}」的備註`} submitLabel="儲存" submitDisabled={note.trim() === (client.note ?? '')}
      busy={busy} error={error} onSubmit={() => { void run(() => onSave(note.trim())) }} onClose={onClose}>
      <NoteField value={note} onChange={setNote} />
    </FormDialog>
  )
}
