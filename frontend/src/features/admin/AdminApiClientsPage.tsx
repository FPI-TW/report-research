import { useState } from 'react'
import {
  Button, Card, ConfirmDialog, ConsolePage, PageHeader, RowActions, Status, isElevationCancelled, toast, useElevate,
  useQueryParam, type RowAction,
} from '../../components/console'
import c from '../../components/console/Console.module.css'
import { RequireAdmin } from '../../components/shell/RequireAdmin'
import type { ApiClientEntitlements, CreateApiClientRequest } from '../../lib/generated/adminApi'
import { ClientDrawer, type ClientAction } from './apiClients/ClientDrawer'
import { CreateClientDialog, LimitsDialog, NoteDialog, ScopeDialog } from './apiClients/ClientDialogs'
import { dimsText, keyPrefixText, marketsText, messageOf } from './apiClients/clientFields'
import { KeyDialog, type ShownKey } from './apiClients/KeyDialog'
import { fmtDateTime } from './auditLabels'
import { RequireScope } from './RequireScope'
import {
  useApiClientActions, useApiClients, type AdminApiClient, type ApiClientPatch, type ApiClientScope,
} from './useAdminApiClients'
import local from './ApiClients.module.css'

type Form = { kind: 'create' } | { kind: 'limits' | 'scope' | 'note'; id: number }
type Pending = { kind: 'disable' | 'rotate'; client: AdminApiClient }

const CONFIRM_TEXT: Record<Pending['kind'], (client: AdminApiClient) => { title: string; body: string; label: string }> = {
  disable: client => ({
    title: `停用「${client.name}」？`,
    body: '下一個請求就生效，對方會收到 401。設定與金鑰都保留，可以隨時重新啟用。',
    label: '停用',
  }),
  rotate: client => ({
    title: `輪替「${client.name}」的金鑰？`,
    body: '舊金鑰在下一個請求就失效。請先確認對方可以馬上換上新金鑰。新金鑰只顯示一次。',
    label: '輪替',
  }),
}

function ApiClients() {
  const clients = useApiClients()
  const { create, update, entitlements, rotate } = useApiClientActions()
  const { elevated, dialog } = useElevate()
  const [openId, setOpenId] = useQueryParam('client')
  const [form, setForm] = useState<Form | null>(null)
  const [pending, setPending] = useState<Pending | null>(null)
  const [shownKey, setShownKey] = useState<ShownKey | null>(null)
  const busy = create.isPending || update.isPending || entitlements.isPending || rotate.isPending
  const byId = (id: number | null) => clients.data?.find(x => x.id === id) ?? null
  const opened = openId == null ? null : byId(Number(openId))
  // 表單的對象取自最新的清單：換一個用戶端就重建表單（key），初值直接取自該用戶端。
  const editing = form && form.kind !== 'create' ? byId(form.id) : null

  /** 建立要已提升：先重新驗證再送。回應裡的原始金鑰只放進一次性對話框，mutation 的快取立刻清掉。 */
  const onCreate = async (body: CreateApiClientRequest) => {
    const res = await elevated('建立 API 用戶端', () => create.mutateAsync(body))
    create.reset()
    setForm(null)
    setShownKey({ name: res.name, key: res.api_key })
    toast(`已建立「${res.name}」`)
  }
  const saveLimits = async (target: AdminApiClient, patch: ApiClientPatch) => {
    await update.mutateAsync({ id: target.id, ...patch })
    setForm(null)
    toast(`已更新「${target.name}」的限流與額度`)
  }
  const saveScope = async (target: AdminApiClient, change: { scopes: ApiClientScope[] | null; entitlements: ApiClientEntitlements | null }) => {
    if (change.scopes) await update.mutateAsync({ id: target.id, scopes: change.scopes })
    if (change.entitlements) await entitlements.mutateAsync({ id: target.id, ...change.entitlements })
    setForm(null)
    toast(`已更新「${target.name}」的授權範圍`)
  }
  const saveNote = async (target: AdminApiClient, note: string) => {
    await update.mutateAsync({ id: target.id, note })
    setForm(null)
    toast(`已更新「${target.name}」的備註`)
  }

  const run = async (p: Pending) => {
    setPending(null)
    try {
      if (p.kind === 'disable') {
        await update.mutateAsync({ id: p.client.id, enabled: false })
        toast(`已停用「${p.client.name}」`)
      } else {
        const res = await elevated('輪替金鑰', () => rotate.mutateAsync(p.client.id))
        rotate.reset()
        setShownKey({ name: res.name, key: res.api_key })
        toast(`已輪替「${res.name}」的金鑰`)
      }
    } catch (err) {
      if (!isElevationCancelled(err)) toast(messageOf(err), { tone: 'bad' })
    }
  }
  const enable = (client: AdminApiClient) => update.mutate({ id: client.id, enabled: true }, {
    onSuccess: () => toast(`已啟用「${client.name}」`), onError: err => toast(messageOf(err), { tone: 'bad' }),
  })

  const start = (action: ClientAction, client: AdminApiClient) => {
    if (action === 'enable') enable(client)
    else if (action === 'disable' || action === 'rotate') setPending({ kind: action, client })
    else setForm({ kind: action, id: client.id })
  }
  /** 列動作：啟用中的主要動作是「輪替金鑰」，停用中的是「啟用」；其餘收進「更多」，停用排最後。 */
  const rowActions = (client: AdminApiClient): { primary: RowAction; more: RowAction[] } => {
    const item = (label: string, action: ClientAction, danger = false): RowAction => ({
      label, danger, onClick: () => start(action, client), disabled: busy, hint: busy ? '處理中，請稍候' : undefined,
    })
    const edit = [item('調整限流與額度', 'limits'), item('變更授權範圍', 'scope'), item('編輯備註', 'note')]
    return client.enabled
      ? { primary: item('輪替金鑰', 'rotate'), more: [...edit, item('停用', 'disable', true)] }
      : { primary: item('啟用', 'enable'), more: [item('輪替金鑰', 'rotate'), ...edit] }
  }

  const confirm = pending ? CONFIRM_TEXT[pending.kind](pending.client) : null

  return (
    <ConsolePage>
      <PageHeader summary="對外 API（/external/v1）的金鑰、限流、每日額度與授權範圍。"
        actions={<Button variant="primary" onClick={() => setForm({ kind: 'create' })}>建立用戶端</Button>}
        help={<>
          每個 API 用戶端一把金鑰，以 <code>Authorization: Bearer</code> 認證，不查也不發 session。
          金鑰只在建立與輪替時顯示一次，系統只保存雜湊值；建立與輪替需要重新驗證身分。
          停用、輪替與設定變更都在對方的下一個請求生效。授權範圍：市場必選；券商、報告類型、商品類型留空＝不限。
          每日額度以台北時間日曆日計。每一筆操作都會留在「操作紀錄」。
        </>} />
      <Card title="API 用戶端清單" labelledBy="api-clients-title"
        hint={clients.data ? `${clients.data.length} 個` : undefined}>
        {clients.isPending ? (
          <p className={c.empty}>載入中…</p>
        ) : clients.isError ? (
          <div className={c.cardBody}><p className={c.error} role="alert">API 用戶端清單載入失敗：{messageOf(clients.error)}</p></div>
        ) : clients.data.length === 0 ? (
          <p className={c.empty}>還沒有任何 API 用戶端</p>
        ) : (
          <div className={c.tableWrap}>
            <table className={c.table}>
              <thead>
                <tr>
                  <th>名稱</th><th>金鑰前綴</th><th>狀態</th><th>端點</th><th>限流／額度</th><th>授權範圍</th>
                  <th>最後使用</th><th className={c.tdActions}>操作</th>
                </tr>
              </thead>
              <tbody>
                {clients.data.map(client => {
                  const actions = rowActions(client)
                  return (
                    <tr key={client.id} className={`${c.rowClickable} ${opened?.id === client.id ? c.rowSelected : ''}`}
                      onClick={() => setOpenId(String(client.id))}>
                      <td className={local.wrap}>
                        <button type="button" className={c.rowLink} aria-label={`${client.name}，查看詳情`}
                          onClick={e => { e.stopPropagation(); setOpenId(String(client.id)) }}>
                          <span className={local.name}>{client.name}</span>
                        </button>
                        {client.note && <div className={c.sub}>{client.note}</div>}
                      </td>
                      <td className={c.num}>{keyPrefixText(client)}</td>
                      {/* 停用是管理員設定的狀態，不是異常：用中性的灰燈（只有異常才亮）。 */}
                      <td><Status tone={client.enabled ? 'ok' : 'idle'}>{client.enabled ? '啟用' : '停用'}</Status></td>
                      <td>{client.scopes.length ? client.scopes.join('、') : <span className={c.muted}>（無）</span>}</td>
                      <td>
                        <div className={c.num}>{client.rate_limit_per_min.toLocaleString()}／分</div>
                        <div className={`${c.sub} ${c.num}`}>{client.daily_quota.toLocaleString()}／日</div>
                      </td>
                      <td className={local.wrap}>
                        <div>{marketsText(client.entitlements)}</div>
                        <div className={c.sub}>{dimsText(client.entitlements)}</div>
                      </td>
                      <td className={client.last_used_at ? c.num : c.muted}>
                        {client.last_used_at ? fmtDateTime(client.last_used_at) : '尚未使用'}
                      </td>
                      <td className={c.tdActions}>
                        <RowActions primary={actions.primary} more={actions.more} label={`${client.name} 的更多操作`} />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <ClientDrawer client={opened} busy={busy} onClose={() => setOpenId(null)} onAction={start} />
      {form?.kind === 'create' && <CreateClientDialog onClose={() => setForm(null)} onCreate={onCreate} />}
      {form?.kind === 'limits' && editing && (
        <LimitsDialog key={editing.id} client={editing} onClose={() => setForm(null)} onSave={patch => saveLimits(editing, patch)} />
      )}
      {form?.kind === 'scope' && editing && (
        <ScopeDialog key={editing.id} client={editing} onClose={() => setForm(null)} onSave={change => saveScope(editing, change)} />
      )}
      {form?.kind === 'note' && editing && (
        <NoteDialog key={editing.id} client={editing} onClose={() => setForm(null)} onSave={note => saveNote(editing, note)} />
      )}
      <ConfirmDialog open={confirm != null} title={confirm?.title ?? ''} body={confirm?.body ?? ''}
        confirmLabel={confirm?.label ?? '確認'} tone="danger"
        onConfirm={() => { if (pending) void run(pending) }} onCancel={() => setPending(null)} />
      <KeyDialog shown={shownKey} onClose={() => setShownKey(null)} />
      {dialog}
    </ConsolePage>
  )
}

/** API 用戶端（/admin/api-clients）。後端 /api/admin/api-clients*（api_clients.manage）。 */
export default function AdminApiClientsPage() {
  return (
    <RequireAdmin>
      <RequireScope scope="api_clients.manage" title="API 用戶端管理"><ApiClients /></RequireScope>
    </RequireAdmin>
  )
}
