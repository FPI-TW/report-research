import { useMemo, useState } from 'react'
import {
  Button, Card, ConfirmDialog, ConsolePage, PageHeader, RowActions, Status, Tabs, isElevationCancelled, toast, useElevate,
  useQueryParam, type RowAction,
} from '../../components/console'
import c from '../../components/console/Console.module.css'
import { RequireAdmin } from '../../components/shell/RequireAdmin'
import type { ApiClientEntitlements, CreateApiClientRequest } from '../../lib/generated/adminApi'
import { endpointsOf, parseMarkdown, withBaseUrl } from './apiClients/apiDoc'
import { ApiDocView } from './apiClients/ApiDocView'
import { ApiTester } from './apiClients/ApiTester'
import { ClientDrawer, type ClientAction } from './apiClients/ClientDrawer'
import { CreateClientDialog, LimitsDialog, NoteDialog, ScopeDialog } from './apiClients/ClientDialogs'
import { dimsText, keyPrefixText, marketsText, messageOf } from './apiClients/clientFields'
import { EXTERNAL_API_MD } from './apiClients/externalApiDoc'
import { KeyDialog, type ShownKey } from './apiClients/KeyDialog'
import { fmtDateTime } from './auditLabels'
import { RequireScope } from './RequireScope'
import {
  useApiClientActions, useApiClients, type AdminApiClient, type ApiClientPatch, type ApiClientScope,
} from './useAdminApiClients'
import local from './ApiClients.module.css'
import { t } from '@/i18n'

type TabKey = 'clients' | 'docs' | 'try'
const TABS: readonly { value: TabKey; label: string }[] = [
  { value: 'clients', get label() { return t('admin.adminApiClientsPage.clients') } },
  { value: 'docs', get label() { return t('admin.adminApiClientsPage.documentation') } },
  { value: 'try', get label() { return t('admin.adminApiClientsPage.tryIt') } },
]

type Form = { kind: 'create' } | { kind: 'limits' | 'scope' | 'note'; id: number }
type Pending = { kind: 'disable' | 'rotate'; client: AdminApiClient }

const CONFIRM_TEXT: Record<Pending['kind'], (client: AdminApiClient) => { title: string; body: string; label: string }> = {
  disable: client => ({
    title: t('admin.adminApiClientsPage.disable', { name: client.name }),
    body: t('admin.adminApiClientsPage.takesEffectOnTheNext'),
    label: t('admin.adminApiClientsPage.disable2'),
  }),
  rotate: client => ({
    title: t('admin.adminApiClientsPage.rotateTheKeyFor', { name: client.name }),
    body: t('admin.adminApiClientsPage.theOldKeyStopsWorking'),
    label: t('admin.adminApiClientsPage.rotate'),
  }),
}

function ApiClients() {
  const clients = useApiClients()
  const { create, update, entitlements, rotate } = useApiClientActions()
  const { elevated, dialog } = useElevate()
  const [openId, setOpenId] = useQueryParam('client')
  const [tabParam, setTabParam] = useQueryParam('tab')
  const tab = TABS.find(item => item.value === tabParam)?.value ?? 'clients'
  // 文件裡的 {BASE_URL} 代入目前站台：在哪個環境下載，交出去的就是那個環境的網址。
  const baseUrl = window.location.origin
  const docMarkdown = useMemo(() => withBaseUrl(EXTERNAL_API_MD, baseUrl), [baseUrl])
  const endpoints = useMemo(() => endpointsOf(parseMarkdown(docMarkdown)), [docMarkdown])
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
    const res = await elevated(t('admin.adminApiClientsPage.createApiClient'), () => create.mutateAsync(body))
    create.reset()
    setForm(null)
    setShownKey({ name: res.name, key: res.api_key })
    toast(t('admin.adminApiClientsPage.created', { name: res.name }))
  }
  const saveLimits = async (target: AdminApiClient, patch: ApiClientPatch) => {
    await update.mutateAsync({ id: target.id, ...patch })
    setForm(null)
    toast(t('admin.adminApiClientsPage.updatedRateLimitAndQuota', { name: target.name }))
  }
  const saveScope = async (target: AdminApiClient, change: { scopes: ApiClientScope[] | null; entitlements: ApiClientEntitlements | null }) => {
    if (change.scopes) await update.mutateAsync({ id: target.id, scopes: change.scopes })
    if (change.entitlements) await entitlements.mutateAsync({ id: target.id, ...change.entitlements })
    setForm(null)
    toast(t('admin.adminApiClientsPage.updatedAccessScopeFor', { name: target.name }))
  }
  const saveNote = async (target: AdminApiClient, note: string) => {
    await update.mutateAsync({ id: target.id, note })
    setForm(null)
    toast(t('admin.adminApiClientsPage.updatedNoteFor', { name: target.name }))
  }

  const run = async (p: Pending) => {
    setPending(null)
    try {
      if (p.kind === 'disable') {
        await update.mutateAsync({ id: p.client.id, enabled: false })
        toast(t('admin.adminApiClientsPage.disabled', { name: p.client.name }))
      } else {
        const res = await elevated(t('admin.adminApiClientsPage.rotateKey'), () => rotate.mutateAsync(p.client.id))
        rotate.reset()
        setShownKey({ name: res.name, key: res.api_key })
        toast(t('admin.adminApiClientsPage.rotatedTheKeyFor', { name: res.name }))
      }
    } catch (err) {
      if (!isElevationCancelled(err)) toast(messageOf(err), { tone: 'bad' })
    }
  }
  const enable = (client: AdminApiClient) => update.mutate({ id: client.id, enabled: true }, {
    onSuccess: () => toast(t('admin.adminApiClientsPage.enabled', { name: client.name })), onError: err => toast(messageOf(err), { tone: 'bad' }),
  })

  const start = (action: ClientAction, client: AdminApiClient) => {
    if (action === 'enable') enable(client)
    else if (action === 'disable' || action === 'rotate') setPending({ kind: action, client })
    else setForm({ kind: action, id: client.id })
  }
  /** 列動作：啟用中的主要動作是「輪替金鑰」，停用中的是「啟用」；其餘收進「更多」，停用排最後。 */
  const rowActions = (client: AdminApiClient): { primary: RowAction; more: RowAction[] } => {
    const item = (label: string, action: ClientAction, danger = false): RowAction => ({
      label, danger, onClick: () => start(action, client), disabled: busy, hint: busy ? t('admin.adminApiClientsPage.workingOnIt') : undefined,
    })
    const edit = [item(t('admin.adminApiClientsPage.adjustRateLimitAndQuota'), 'limits'), item(t('admin.adminApiClientsPage.changeAccessScope'), 'scope'), item(t('admin.adminApiClientsPage.editNote'), 'note')]
    return client.enabled
      ? { primary: item(t('admin.adminApiClientsPage.rotateKey'), 'rotate'), more: [...edit, item(t('admin.adminApiClientsPage.disable2'), 'disable', true)] }
      : { primary: item(t('admin.adminApiClientsPage.enable'), 'enable'), more: [item(t('admin.adminApiClientsPage.rotateKey'), 'rotate'), ...edit] }
  }

  const confirm = pending ? CONFIRM_TEXT[pending.kind](pending.client) : null

  return (
    <ConsolePage>
      <PageHeader summary={t('admin.adminApiClientsPage.keysRateLimitsDailyQuotas')}
        actions={<Button variant="primary" onClick={() => setForm({ kind: 'create' })}>{t('admin.adminApiClientsPage.createClient')}</Button>}
        help={<>
          {t('admin.adminApiClientsPage.eachApiClientHasOne')}{' '}<code>Authorization: Bearer</code>{' '}{t('admin.adminApiClientsPage.noSessionIsCheckedOr')}
        </>} />
      <Tabs label={t('admin.adminApiClientsPage.apiClientTabs')} tabs={TABS} value={tab} onChange={v => setTabParam(v === 'clients' ? null : v)} />
      {tab === 'docs' && <ApiDocView markdown={docMarkdown} baseUrl={baseUrl} />}
      {tab === 'try' && <ApiTester endpoints={endpoints} baseUrl={baseUrl} />}
      {tab === 'clients' && <Card title={t('admin.adminApiClientsPage.apiClientList')} labelledBy="api-clients-title"
        hint={clients.data ? t('admin.adminApiClientsPage.clients2', { length: clients.data.length }) : undefined}>
        {clients.isPending ? (
          <p className={c.empty}>{t('admin.adminApiClientsPage.loading')}</p>
        ) : clients.isError ? (
          <div className={c.cardBody}><p className={c.error} role="alert">{t('admin.adminApiClientsPage.couldnTLoadApiClients', { error: messageOf(clients.error) })}</p></div>
        ) : clients.data.length === 0 ? (
          <p className={c.empty}>{t('admin.adminApiClientsPage.noApiClientsYet')}</p>
        ) : (
          <div className={c.tableWrap}>
            <table className={c.table}>
              <thead>
                <tr>
                  <th>{t('admin.adminApiClientsPage.name')}</th><th>{t('admin.adminApiClientsPage.keyPrefix')}</th><th>{t('admin.adminApiClientsPage.status')}</th><th>{t('admin.adminApiClientsPage.endpoints')}</th><th>{t('admin.adminApiClientsPage.rateLimitQuota')}</th><th>{t('admin.adminApiClientsPage.accessScope')}</th>
                  <th>{t('admin.adminApiClientsPage.lastUsed')}</th><th className={`${c.tdActions} ${c.stickyRight}`}>{t('admin.adminApiClientsPage.actions')}</th>
                </tr>
              </thead>
              <tbody>
                {clients.data.map(client => {
                  const actions = rowActions(client)
                  return (
                    <tr key={client.id} className={`${c.rowClickable} ${opened?.id === client.id ? c.rowSelected : ''}`}
                      onClick={() => setOpenId(String(client.id))}>
                      <td className={`${local.wrap} ${local.nameCell}`}>
                        <button type="button" className={c.rowLink} aria-label={t('admin.adminApiClientsPage.viewDetails', { name: client.name })}
                          onClick={e => { e.stopPropagation(); setOpenId(String(client.id)) }}>
                          <span className={local.name}>{client.name}</span>
                        </button>
                        {client.note && <div className={`${c.sub} ${c.clamp2}`} title={client.note}>{client.note}</div>}
                      </td>
                      <td className={c.num}>{keyPrefixText(client)}</td>
                      {/* 停用是管理員設定的狀態，不是異常：用中性的灰燈（只有異常才亮）。 */}
                      <td><Status tone={client.enabled ? 'ok' : 'idle'}>{client.enabled ? t('admin.adminApiClientsPage.enable') : t('admin.adminApiClientsPage.disable2')}</Status></td>
                      <td>{client.scopes.length ? client.scopes.join(t('admin.adminApiClientsPage.text')) : <span className={c.muted}>{t('admin.adminApiClientsPage.none')}</span>}</td>
                      <td>
                        <div className={c.num}>{t('admin.adminApiClientsPage.min', { rate_limit_per_min: client.rate_limit_per_min.toLocaleString() })}</div>
                        <div className={`${c.sub} ${c.num}`}>{t('admin.adminApiClientsPage.day', { daily_quota: client.daily_quota.toLocaleString() })}</div>
                      </td>
                      <td className={local.wrap}>
                        <div>{marketsText(client.entitlements)}</div>
                        <div className={c.sub}>{dimsText(client.entitlements)}</div>
                      </td>
                      <td className={client.last_used_at ? c.num : c.muted}>
                        {client.last_used_at ? fmtDateTime(client.last_used_at) : t('admin.adminApiClientsPage.neverUsed')}
                      </td>
                      <td className={`${c.tdActions} ${c.stickyRight}`}>
                        <RowActions primary={actions.primary} more={actions.more} label={t('admin.adminApiClientsPage.moreActionsFor', { name: client.name })} />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>}
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
        confirmLabel={confirm?.label ?? t('admin.adminApiClientsPage.confirm')} tone="danger"
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
      <RequireScope scope="api_clients.manage" title={t('admin.adminApiClientsPage.apiClients')}><ApiClients /></RequireScope>
    </RequireAdmin>
  )
}
