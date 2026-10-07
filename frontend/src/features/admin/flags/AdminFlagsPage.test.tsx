import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import AdminFlagsPage from './AdminFlagsPage'

afterEach(() => vi.unstubAllGlobals())

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })

const item = (key: string, over: Record<string, unknown> = {}) => ({
  key, description: `${key} 的說明`, ceiling_env: 'X_ENABLED', ceiling: true, default: true,
  override: null, effective: 'on', effective_for_me: true, ...over,
})

const LIST = {
  registry_version: 'abc123abc123',
  ignored_keys: ['legacy.flag'],
  items: [
    item('ask.web_search', { ceiling_env: 'ASK_ENABLE_WEB', ceiling: false, effective: 'off', effective_for_me: false }),
    item('qa.agentic', {
      ceiling_env: 'QA_AGENTIC_ENABLED', effective: 'scoped',
      override: { enabled: true, allow_roles: ['admin'], allow_users: [{ id: 'u2', username: 'alice' }],
        note: '試用中', updated_by: 'ops', updated_at: '2026-10-07T08:00:00Z' },
    }),
    item('quota.enforce', { ceiling_env: 'QUOTA_ENFORCE', default: false, effective: 'off', effective_for_me: false }),
  ],
}

type Handler = (init?: RequestInit) => Response
function stub(scopes: string[], handlers: Record<string, Handler> = {}) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/me') {
      return json({ id: 'u1', username: 'ops', role: 'admin', is_super: false, scopes, totp_enabled: false })
    }
    const h = handlers[`${init?.method ?? 'GET'} ${url}`]
    if (h) return h(init)
    if (url === '/api/admin/flags') return json(LIST)
    if (url === '/api/admin/users') return json({ items: [
      { id: 'u2', username: 'alice', role: 'user', enabled: true },
      { id: 'u3', username: 'bob', role: 'user', enabled: true },
    ] })
    return json({ detail: 'not found', code: 'not_found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><AdminFlagsPage /></QueryClientProvider>)
}

const card = async (key: string) => (await screen.findByRole('heading', { name: key })).closest('article') as HTMLElement
const sent = (m: ReturnType<typeof stub>, key: string) =>
  m.mock.calls.filter(([url, init]) => `${(init as RequestInit | undefined)?.method ?? 'GET'} ${url}` === key)

test('每個旗標顯示環境上限、覆寫、實際值；上限關閉時說明要先在環境檔開啟、調整停用', async () => {
  stub(['admin', 'ops.read', 'ops.operate'])
  wrap()
  const web = await card('ask.web_search')
  expect(within(web).getByText('對所有人關閉')).toBeInTheDocument()
  expect(within(web).getByText(/需先在環境檔開啟/)).toBeInTheDocument()
  expect(within(web).getByRole('button', { name: '調整' })).toBeDisabled()

  const agentic = await card('qa.agentic')
  expect(within(agentic).getByText('只對部分使用者開啟')).toBeInTheDocument()
  expect(within(agentic).getByText('限定開啟：管理員；alice')).toBeInTheDocument()
  expect(within(agentic).getByText('註記：試用中')).toBeInTheDocument()
  expect(within(agentic).getByRole('button', { name: '恢復預設' })).toBeEnabled()

  const quota = await card('quota.enforce')
  expect(within(quota).getByText('沒有覆寫（預設關閉）')).toBeInTheDocument()
  expect(screen.getByText(/程式沒登記的旗標/)).toBeInTheDocument()
})

test('沒有 ops.operate：只能看與匯出，沒有調整與匯入', async () => {
  stub(['admin', 'ops.read'])
  wrap()
  await card('qa.agentic')
  expect(screen.queryByRole('button', { name: '調整' })).not.toBeInTheDocument()
  expect(screen.queryByLabelText(/匯入檔案/)).not.toBeInTheDocument()
  expect(screen.getByText('匯入需要 ops.operate 權限')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '匯出設定（JSON）' })).toBeEnabled()
})

test('調整：全站關閉送出 PUT；需要重新驗證時彈出驗證框、驗證後自動重試', async () => {
  let elevated = false
  const m = stub(['admin', 'ops.read', 'ops.operate'], {
    'PUT /api/admin/flags/qa.agentic': () => elevated
      ? json(item('qa.agentic', { override: { enabled: false }, effective: 'off' }))
      : json({ detail: '這項操作需要重新驗證密碼', code: 'elevation_required' }, 403),
    'POST /api/admin/elevate': () => { elevated = true; return json({ elevated_until: '2026-10-07T09:00:00Z' }) },
  })
  wrap()
  fireEvent.click(within(await card('qa.agentic')).getByRole('button', { name: '調整' }))
  const dialog = await screen.findByRole('dialog', { name: '調整「qa.agentic」' })
  fireEvent.click(within(dialog).getByLabelText('全站關閉（降級）'))
  fireEvent.change(within(dialog).getByLabelText(/註記/), { target: { value: '  CPU 吃緊  ' } })
  fireEvent.click(within(dialog).getByRole('button', { name: '儲存' }))
  const pw = await screen.findByLabelText(/密碼/)
  fireEvent.change(pw, { target: { value: 'pw' } })
  fireEvent.click(screen.getByRole('button', { name: /驗證|確認/ }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已更新「qa.agentic」'))
  const puts = sent(m, 'PUT /api/admin/flags/qa.agentic')
  expect(puts).toHaveLength(2)
  expect(JSON.parse(String((puts[1][1] as RequestInit).body))).toEqual(
    { enabled: false, allow_roles: null, allow_users: null, note: 'CPU 吃緊' })
})

test('調整：限定開啟沒勾任何角色或使用者時不送出', async () => {
  const m = stub(['admin', 'ops.read', 'ops.operate'])
  wrap()
  fireEvent.click(within(await card('quota.enforce')).getByRole('button', { name: '調整' }))
  const dialog = await screen.findByRole('dialog')
  fireEvent.click(within(dialog).getByLabelText('只對部分使用者開啟'))
  await within(dialog).findByLabelText(/alice/)
  fireEvent.click(within(dialog).getByRole('button', { name: '儲存' }))
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('至少要勾一個角色或一位使用者')
  expect(sent(m, 'PUT /api/admin/flags/quota.enforce')).toHaveLength(0)
  fireEvent.click(within(dialog).getByLabelText(/alice/))
  fireEvent.click(within(dialog).getByRole('button', { name: '儲存' }))
  await waitFor(() => expect(sent(m, 'PUT /api/admin/flags/quota.enforce')).toHaveLength(1))
  expect(JSON.parse(String((sent(m, 'PUT /api/admin/flags/quota.enforce')[0][1] as RequestInit).body)))
    .toEqual({ enabled: true, allow_roles: null, allow_users: ['u2'], note: null })
})

test('恢復預設送出 DELETE', async () => {
  const m = stub(['admin', 'ops.read', 'ops.operate'], {
    'DELETE /api/admin/flags/qa.agentic': () => json(item('qa.agentic')),
  })
  wrap()
  fireEvent.click(within(await card('qa.agentic')).getByRole('button', { name: '恢復預設' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('「qa.agentic」已恢復預設'))
  expect(sent(m, 'DELETE /api/admin/flags/qa.agentic')).toHaveLength(1)
})

const DOC = {
  format: 'report-mark/feature-flags', format_version: 1, registry_version: 'zzz', source_environment: 'staging',
  flags: [{ key: 'qa.agentic', override: { enabled: false } }, { key: 'brand.new', override: { enabled: true } }],
}

test('匯入：先預覽差異；有錯誤時不能套用', async () => {
  const m = stub(['admin', 'ops.read', 'ops.operate'], {
    'POST /api/admin/flags/import': () => json({
      dry_run: true, applied: false, registry_version: 'abc123abc123', document_registry_version: 'zzz',
      registry_version_match: false,
      changes: [{ key: 'qa.agentic', action: 'update', before: { enabled: true, allow_roles: ['admin'] }, after: { enabled: false } }],
      errors: [{ key: 'brand.new', code: 'unknown_key', detail: '這個環境沒有旗標 brand.new' }],
    }),
  })
  wrap()
  await card('qa.agentic')
  const file = new File([JSON.stringify(DOC)], 'flags.json', { type: 'application/json' })
  fireEvent.change(screen.getByLabelText('匯入檔案並預覽'), { target: { files: [file] } })
  const preview = await screen.findByLabelText('匯入預覽')
  expect(within(preview).getByText('更新覆寫')).toBeInTheDocument()
  expect(within(preview).getByText('全站關閉')).toBeInTheDocument()
  expect(within(preview).getByText(/這個環境沒有旗標 brand.new/)).toBeInTheDocument()
  expect(within(preview).getByText(/旗標清單版本/)).toBeInTheDocument()
  expect(within(preview).getByRole('button', { name: '套用 1 項變更' })).toBeDisabled()
  const body = JSON.parse(String((sent(m, 'POST /api/admin/flags/import')[0][1] as RequestInit).body))
  expect(body.dry_run).toBe(true)
  expect(body.document.flags).toHaveLength(2)
})

test('匯入：預覽沒有錯誤就能套用', async () => {
  let applied = false
  const m = stub(['admin', 'ops.read', 'ops.operate'], {
    'POST /api/admin/flags/import': (init) => {
      const dry = JSON.parse(String(init?.body)).dry_run
      if (!dry) applied = true
      return json({
        dry_run: dry, applied: !dry, registry_version: 'abc123abc123', document_registry_version: 'abc123abc123',
        registry_version_match: true,
        changes: [{ key: 'qa.agentic', action: 'create', before: null, after: { enabled: false } },
          { key: 'ask.rerank', action: 'unchanged', before: null, after: null }],
        errors: [],
      })
    },
  })
  wrap()
  await card('qa.agentic')
  const ok = { ...DOC, flags: [DOC.flags[0], { key: 'ask.rerank', override: null }] }
  fireEvent.change(screen.getByLabelText('匯入檔案並預覽'),
    { target: { files: [new File([JSON.stringify(ok)], 'flags.json', { type: 'application/json' })] } })
  const preview = await screen.findByLabelText('匯入預覽')
  fireEvent.click(within(preview).getByRole('button', { name: '套用 1 項變更' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已套用匯入：1 個旗標有變更'))
  expect(applied).toBe(true)
  expect(sent(m, 'POST /api/admin/flags/import')).toHaveLength(2)
})

test('匯入：不是匯出檔就不送出', async () => {
  const m = stub(['admin', 'ops.read', 'ops.operate'])
  wrap()
  await card('qa.agentic')
  fireEvent.change(screen.getByLabelText('匯入檔案並預覽'),
    { target: { files: [new File(['{"hello": 1}'], 'x.json', { type: 'application/json' })] } })
  expect(await screen.findByRole('alert')).toHaveTextContent('這不是功能開關的匯出檔')
  expect(sent(m, 'POST /api/admin/flags/import')).toHaveLength(0)
})
