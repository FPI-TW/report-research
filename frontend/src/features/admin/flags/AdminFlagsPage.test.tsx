import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { Toaster, dismissToast } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import AdminFlagsPage from './AdminFlagsPage'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  dismissToast()
  Reflect.deleteProperty(URL, 'createObjectURL')
  Reflect.deleteProperty(URL, 'revokeObjectURL')
})

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

const OPERATOR = ['admin', 'ops.read', 'ops.operate']
const ELEVATION_REQUIRED = () => json({ detail: '這項操作需要重新驗證密碼', code: 'elevation_required' }, 403)

type Handler = (init?: RequestInit) => Response
interface StubOptions {
  /** `/api/me` 回報的重新驗證到期時間：null＝還沒驗證（操作前會先問）。預設是還在 10 分鐘視窗內。 */
  elevatedUntil?: string | null
}

function stub(scopes: string[], handlers: Record<string, Handler> = {}, { elevatedUntil }: StubOptions = {}) {
  const until = elevatedUntil === undefined ? new Date(Date.now() + 300_000).toISOString() : elevatedUntil
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/me') {
      return json({ id: 'u1', username: 'ops', role: 'admin', is_super: false, scopes, totp_enabled: false, elevated_until: until })
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
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/flags']}>
        <AdminFlagsPage />
        <Toaster />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const row = async (key: string) => (await screen.findByText(key)).closest('tr') as HTMLElement
const sent = (m: ReturnType<typeof stub>, key: string) =>
  m.mock.calls.filter(([url, init]) => `${(init as RequestInit | undefined)?.method ?? 'GET'} ${url}` === key)
const bodyOf = (call: unknown[]) => JSON.parse(String((call[1] as RequestInit).body))

/** 在「重新驗證身分」輸入密碼並送出。 */
async function reverify(what: string) {
  const elevate = await screen.findByRole('dialog', { name: '重新驗證身分' })
  expect(within(elevate).getByText(new RegExp(`「${what}」需要重新驗證`))).toBeInTheDocument()
  fireEvent.change(within(elevate).getByLabelText('密碼'), { target: { value: 'fixed-test-secret-elevate1' } })
  fireEvent.click(within(elevate).getByRole('button', { name: '驗證' }))
}

test('表格：每個旗標的實際值、對我、環境上限、覆寫與註記、最後修改；上限關閉時「調整」停用並說明原因', async () => {
  stub(OPERATOR)
  wrap()
  expect(await screen.findByRole('heading', { level: 1, name: '功能旗標' })).toBeInTheDocument()
  const table = await screen.findByRole('region', { name: '旗標' })
  expect(within(table).getByText('3 個')).toBeInTheDocument()

  const web = await row('ask.web_search')
  expect(within(web).getByText('ask.web_search 的說明')).toBeInTheDocument()
  expect(within(web).getByText('對所有人關閉')).toBeInTheDocument()
  expect(within(web).getByText('ASK_ENABLE_WEB 關')).toBeInTheDocument()
  expect(within(web).getByText(/需先在環境檔開啟/)).toBeInTheDocument()
  const adjust = within(web).getByRole('button', { name: '調整' })
  expect(adjust).toBeDisabled()
  expect(adjust).toHaveAttribute('title', '環境上限關閉：先在環境檔設 ASK_ENABLE_WEB=1 並重啟 web')

  const agentic = await row('qa.agentic')
  expect(within(agentic).getByText('只對部分使用者開啟')).toBeInTheDocument()
  expect(within(agentic).getByTitle('這個功能現在對你的帳號是否生效')).toHaveTextContent('開')
  expect(within(agentic).getByText('QA_AGENTIC_ENABLED 開')).toBeInTheDocument()
  expect(within(agentic).getByText('限定開啟：管理員；alice')).toBeInTheDocument()
  // 註記在清單裡最多兩行，完整內容放 title（調整表單裡也有）
  expect(within(agentic).getByText('註記：試用中')).toHaveAttribute('title', '試用中')
  expect(within(agentic).getByText('註記：試用中')).toHaveClass(c.clamp2)
  expect(within(agentic).getByText('ops')).toBeInTheDocument()
  expect(within(agentic).getByRole('button', { name: '調整' })).toBeEnabled()
  // 恢復預設收在「更多」裡；有覆寫才有。
  fireEvent.click(within(agentic).getByRole('button', { name: 'qa.agentic 的更多操作' }))
  expect(screen.getByRole('menuitem', { name: '恢復預設' })).toBeEnabled()

  const quota = await row('quota.enforce')
  expect(within(quota).getByText('沒有覆寫（預設關閉）')).toBeInTheDocument()
  expect(within(quota).getByTitle('這個功能現在對你的帳號是否生效')).toHaveTextContent('關')
  expect(within(quota).queryByRole('button', { name: 'quota.enforce 的更多操作' })).not.toBeInTheDocument()
  expect(screen.getByText(/程式沒登記的旗標/)).toHaveTextContent('legacy.flag')

  // 操作欄有表頭、釘在右邊：表格橫向捲動時還看得到
  expect(within(table).getByRole('columnheader', { name: '操作' })).toHaveClass(c.stickyRight)
  for (const tr of [web, agentic, quota]) expect(within(tr).getAllByRole('cell').at(-1)).toHaveClass(c.stickyRight)

  // 長說明收在「說明」後面
  expect(screen.queryByText(/安全相關設定不在這裡/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '說明' }))
  expect(screen.getByText(/安全相關設定不在這裡/)).toBeInTheDocument()
})

test('清單載入失敗：顯示後端的 detail', async () => {
  stub(OPERATOR, { 'GET /api/admin/flags': () => json({ detail: '資料庫暫時無法使用，請稍後再試', code: 'db_unavailable' }, 503) })
  wrap()
  expect(await screen.findByRole('alert')).toHaveTextContent('功能旗標載入失敗：資料庫暫時無法使用，請稍後再試')
  expect(screen.queryByRole('region', { name: '旗標' })).not.toBeInTheDocument()
})

test('沒有 ops.operate：只能看與匯出，沒有調整與匯入', async () => {
  stub(['admin', 'ops.read'])
  wrap()
  await row('qa.agentic')
  // 沒有列動作就沒有操作欄
  expect(screen.queryByRole('columnheader', { name: '操作' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '調整' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /的更多操作/ })).not.toBeInTheDocument()
  expect(screen.queryByLabelText(/匯入檔案/)).not.toBeInTheDocument()
  const importBtn = screen.getByRole('button', { name: '匯入' })
  expect(importBtn).toBeDisabled()
  expect(importBtn).toHaveAttribute('title', '匯入需要 ops.operate 權限')
  expect(screen.getByRole('button', { name: '匯出 JSON' })).toBeEnabled()
  expect(screen.getByRole('note')).toHaveTextContent('修改需要另外授予的 ops.operate 權限')
})

test('沒有 ops.read：只說明需要的權限，不打旗標 API', async () => {
  const m = stub(['admin'])
  wrap()
  expect(await screen.findByRole('heading', { name: '需要「功能旗標」權限' })).toBeInTheDocument()
  expect(sent(m, 'GET /api/admin/flags')).toHaveLength(0)
})

test('調整：先重新驗證身分才送出 PUT；成功後關閉表單並用 toast 回報', async () => {
  let elevated = false
  const m = stub(OPERATOR, {
    'PUT /api/admin/flags/qa.agentic': () => elevated
      ? json(item('qa.agentic', { override: { enabled: false }, effective: 'off' }))
      : ELEVATION_REQUIRED(),
    'POST /api/me/elevate': () => { elevated = true; return json({ elevated_until: new Date(Date.now() + 600_000).toISOString() }) },
  }, { elevatedUntil: null })
  wrap()
  fireEvent.click(within(await row('qa.agentic')).getByRole('button', { name: '調整' }))
  const dialog = await screen.findByRole('dialog', { name: '調整「qa.agentic」' })
  expect(within(dialog).getByText('qa.agentic 的說明')).toBeInTheDocument()
  // 初值取自現有的覆寫：限定開啟＋管理員＋alice＋註記
  expect(within(dialog).getByRole('button', { name: '限定開啟' })).toHaveAttribute('aria-pressed', 'true')
  expect(within(dialog).getByLabelText('管理員')).toBeChecked()
  expect(await within(dialog).findByLabelText(/alice/)).toBeChecked()
  expect(within(dialog).getByLabelText(/註記/)).toHaveValue('試用中')

  fireEvent.click(within(dialog).getByRole('button', { name: '全站關閉' }))
  fireEvent.change(within(dialog).getByLabelText(/註記/), { target: { value: '  CPU 吃緊  ' } })
  fireEvent.click(within(dialog).getByRole('button', { name: '儲存' }))
  await reverify('變更功能旗標')
  expect(sent(m, 'PUT /api/admin/flags/qa.agentic')).toHaveLength(0)
  expect(await screen.findByRole('status')).toHaveTextContent('已更新「qa.agentic」，已寫入操作紀錄並立即生效')
  const puts = sent(m, 'PUT /api/admin/flags/qa.agentic')
  expect(puts).toHaveLength(1)
  expect(bodyOf(puts[0])).toEqual({ enabled: false, allow_roles: null, allow_users: null, note: 'CPU 吃緊' })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('調整：前端以為還在驗證視窗內、後端卻要求重新驗證時，彈出驗證框，驗證後自動重試', async () => {
  let elevated = false
  const m = stub(OPERATOR, {
    'PUT /api/admin/flags/qa.agentic': () => elevated ? json(item('qa.agentic', { effective: 'on' })) : ELEVATION_REQUIRED(),
    'POST /api/me/elevate': () => { elevated = true; return json({ elevated_until: new Date(Date.now() + 600_000).toISOString() }) },
  })
  wrap()
  fireEvent.click(within(await row('qa.agentic')).getByRole('button', { name: '調整' }))
  const dialog = await screen.findByRole('dialog', { name: '調整「qa.agentic」' })
  fireEvent.click(within(dialog).getByRole('button', { name: '全站開啟' }))
  fireEvent.click(within(dialog).getByRole('button', { name: '儲存' }))
  await reverify('變更功能旗標')
  expect(await screen.findByRole('status')).toHaveTextContent('已更新「qa.agentic」')
  const puts = sent(m, 'PUT /api/admin/flags/qa.agentic')
  expect(puts).toHaveLength(2)
  expect(bodyOf(puts[1])).toEqual({ enabled: true, allow_roles: null, allow_users: null, note: '試用中' })
})

test('調整：重新驗證按取消，表單留著、不送出、不當成錯誤；後端的錯誤顯示在表單裡', async () => {
  const m = stub(OPERATOR, {
    'PUT /api/admin/flags/quota.enforce': () => json({ detail: '環境上限已關閉', code: 'flag_ceiling_off' }, 409),
    'POST /api/me/elevate': () => json({ elevated_until: new Date(Date.now() + 600_000).toISOString() }),
  }, { elevatedUntil: null })
  wrap()
  fireEvent.click(within(await row('quota.enforce')).getByRole('button', { name: '調整' }))
  const dialog = await screen.findByRole('dialog', { name: '調整「quota.enforce」' })
  // 沒有覆寫：初值跟著預設（關閉）
  expect(within(dialog).getByRole('button', { name: '全站關閉' })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(within(dialog).getByRole('button', { name: '全站開啟' }))
  fireEvent.click(within(dialog).getByRole('button', { name: '儲存' }))
  fireEvent.click(within(await screen.findByRole('dialog', { name: '重新驗證身分' })).getByRole('button', { name: '取消' }))
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '重新驗證身分' })).not.toBeInTheDocument())
  expect(screen.getByRole('dialog', { name: '調整「quota.enforce」' })).toBeInTheDocument()
  expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument()
  expect(sent(m, 'PUT /api/admin/flags/quota.enforce')).toHaveLength(0)
  expect(screen.queryByRole('status')).not.toBeInTheDocument()

  fireEvent.click(within(dialog).getByRole('button', { name: '儲存' }))
  await reverify('變更功能旗標')
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('環境上限已關閉')
  expect(screen.getByRole('dialog', { name: '調整「quota.enforce」' })).toBeInTheDocument()
})

test('調整：限定開啟沒勾任何角色或帳號時不送出', async () => {
  const m = stub(OPERATOR, { 'PUT /api/admin/flags/quota.enforce': () => json(item('quota.enforce', { effective: 'scoped' })) })
  wrap()
  fireEvent.click(within(await row('quota.enforce')).getByRole('button', { name: '調整' }))
  const dialog = await screen.findByRole('dialog', { name: '調整「quota.enforce」' })
  expect(within(dialog).queryByRole('group', { name: '限定角色' })).not.toBeInTheDocument()
  fireEvent.click(within(dialog).getByRole('button', { name: '限定開啟' }))
  expect(within(dialog).getByRole('group', { name: '限定角色' })).toBeInTheDocument()
  await within(dialog).findByLabelText(/alice/)
  fireEvent.click(within(dialog).getByRole('button', { name: '儲存' }))
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('限定開啟至少要勾一個角色或一位使用者')
  expect(sent(m, 'PUT /api/admin/flags/quota.enforce')).toHaveLength(0)
  fireEvent.click(within(dialog).getByLabelText(/alice/))
  fireEvent.click(within(dialog).getByRole('button', { name: '儲存' }))
  await waitFor(() => expect(sent(m, 'PUT /api/admin/flags/quota.enforce')).toHaveLength(1))
  expect(bodyOf(sent(m, 'PUT /api/admin/flags/quota.enforce')[0]))
    .toEqual({ enabled: true, allow_roles: null, allow_users: ['u2'], note: null })
})

test('恢復預設：先確認（可逆、金色），再重新驗證，送出 DELETE 後用 toast 回報', async () => {
  const m = stub(OPERATOR, {
    'DELETE /api/admin/flags/qa.agentic': () => json(item('qa.agentic')),
    'POST /api/me/elevate': () => json({ elevated_until: new Date(Date.now() + 600_000).toISOString() }),
  }, { elevatedUntil: null })
  wrap()
  fireEvent.click(within(await row('qa.agentic')).getByRole('button', { name: 'qa.agentic 的更多操作' }))
  fireEvent.click(screen.getByRole('menuitem', { name: '恢復預設' }))
  const confirm = await screen.findByRole('alertdialog', { name: '「qa.agentic」恢復預設？' })
  expect(within(confirm).getByText('刪除這筆覆寫，回到程式內建的預設值。')).toBeInTheDocument()
  expect(sent(m, 'DELETE /api/admin/flags/qa.agentic')).toHaveLength(0)
  fireEvent.click(within(confirm).getByRole('button', { name: '恢復預設' }))
  await reverify('變更功能旗標')
  expect(await screen.findByRole('status')).toHaveTextContent('「qa.agentic」已恢復預設')
  expect(sent(m, 'DELETE /api/admin/flags/qa.agentic')).toHaveLength(1)
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
})

test('恢復預設：確認框按取消不送出；後端失敗用 toast 講明', async () => {
  const m = stub(OPERATOR, {
    'DELETE /api/admin/flags/qa.agentic': () => json({ detail: '資料庫暫時無法使用', code: 'db_unavailable' }, 503),
  })
  wrap()
  const agentic = await row('qa.agentic')
  fireEvent.click(within(agentic).getByRole('button', { name: 'qa.agentic 的更多操作' }))
  fireEvent.click(screen.getByRole('menuitem', { name: '恢復預設' }))
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '取消' }))
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  expect(sent(m, 'DELETE /api/admin/flags/qa.agentic')).toHaveLength(0)

  fireEvent.click(within(agentic).getByRole('button', { name: 'qa.agentic 的更多操作' }))
  fireEvent.click(screen.getByRole('menuitem', { name: '恢復預設' }))
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '恢復預設' }))
  const status = await screen.findByRole('status')
  expect(status).toHaveTextContent('資料庫暫時無法使用')
  expect(status).toHaveAttribute('data-tone', 'bad')
  await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
})

const DOC = {
  format: 'report-mark/feature-flags', format_version: 1, registry_version: 'zzz', source_environment: 'staging',
  flags: [{ key: 'qa.agentic', override: { enabled: false } }, { key: 'brand.new', override: { enabled: true } }],
}
const pickFile = (content: string, name = 'flags.json') =>
  fireEvent.change(screen.getByLabelText('匯入檔案並預覽'),
    { target: { files: [new File([content], name, { type: 'application/json' })] } })

test('匯出 JSON：下載目前的覆寫，檔名帶來源環境', async () => {
  const m = stub(['admin', 'ops.read'], { 'GET /api/admin/flags/export': () => json(DOC) })
  // jsdom 沒有 createObjectURL：這一題自己補上，結束時拿掉。
  const blobs: Blob[] = []
  URL.createObjectURL = (b: Blob | MediaSource) => { blobs.push(b as Blob); return 'blob:flags' }
  URL.revokeObjectURL = () => {}
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  wrap()
  await row('qa.agentic')
  fireEvent.click(screen.getByRole('button', { name: '匯出 JSON' }))
  expect(await screen.findByRole('status')).toHaveTextContent(/已下載 report-mark-flags-staging-\d{8}\.json/)
  expect(sent(m, 'GET /api/admin/flags/export')).toHaveLength(1)
  expect(click).toHaveBeenCalledTimes(1)
  expect(JSON.parse(await blobs[0].text())).toEqual(DOC)
})

test('匯出失敗用 toast 講明', async () => {
  stub(['admin', 'ops.read'], { 'GET /api/admin/flags/export': () => json({ detail: '資料庫暫時無法使用' }, 503) })
  wrap()
  await row('qa.agentic')
  fireEvent.click(screen.getByRole('button', { name: '匯出 JSON' }))
  const status = await screen.findByRole('status')
  expect(status).toHaveTextContent('匯出失敗：資料庫暫時無法使用')
  expect(status).toHaveAttribute('data-tone', 'bad')
})

test('匯入：先預覽差異；有錯誤時不能套用', async () => {
  const m = stub(OPERATOR, {
    'POST /api/admin/flags/import': () => json({
      dry_run: true, applied: false, registry_version: 'abc123abc123', document_registry_version: 'zzz',
      registry_version_match: false,
      changes: [{ key: 'qa.agentic', action: 'update', before: { enabled: true, allow_roles: ['admin'] }, after: { enabled: false } }],
      errors: [{ key: 'brand.new', code: 'unknown_key', detail: '這個環境沒有旗標 brand.new' }],
    }),
  })
  wrap()
  await row('qa.agentic')
  expect(screen.getByRole('button', { name: '匯入' })).toBeEnabled()
  pickFile(JSON.stringify(DOC))
  const preview = await screen.findByRole('dialog', { name: '匯入預覽' })
  expect(within(preview).getByText('更新覆寫')).toBeInTheDocument()
  expect(within(preview).getByText('限定開啟：角色 admin')).toBeInTheDocument()
  expect(within(preview).getByText('全站關閉')).toBeInTheDocument()
  expect(within(preview).getByRole('alert')).toHaveTextContent('這個環境沒有旗標 brand.new')
  expect(within(preview).getByText(/旗標清單版本/)).toBeInTheDocument()
  expect(within(preview).getByRole('button', { name: '套用 1 項變更' })).toBeDisabled()
  const body = bodyOf(sent(m, 'POST /api/admin/flags/import')[0])
  expect(body.dry_run).toBe(true)
  expect(body.document.flags).toHaveLength(2)
  fireEvent.click(within(preview).getByRole('button', { name: '取消' }))
  expect(screen.queryByRole('dialog', { name: '匯入預覽' })).not.toBeInTheDocument()
  expect(sent(m, 'POST /api/admin/flags/import')).toHaveLength(1)
})

test('匯入：預覽沒有錯誤就能套用', async () => {
  let applied = false
  const m = stub(OPERATOR, {
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
  await row('qa.agentic')
  pickFile(JSON.stringify({ ...DOC, flags: [DOC.flags[0], { key: 'ask.rerank', override: null }] }))
  const preview = await screen.findByRole('dialog', { name: '匯入預覽' })
  expect(within(preview).queryByText(/旗標清單版本/)).not.toBeInTheDocument()
  expect(within(preview).getByText('不變')).toBeInTheDocument()
  fireEvent.click(within(preview).getByRole('button', { name: '套用 1 項變更' }))
  expect(await screen.findByRole('status')).toHaveTextContent('已套用匯入：1 個旗標有變更')
  expect(applied).toBe(true)
  expect(sent(m, 'POST /api/admin/flags/import')).toHaveLength(2)
  expect(screen.queryByRole('dialog', { name: '匯入預覽' })).not.toBeInTheDocument()
})

test('匯入：預覽前先重新驗證身分；取消就不送出', async () => {
  let elevated = false
  const m = stub(OPERATOR, {
    'POST /api/admin/flags/import': () => elevated
      ? json({ dry_run: true, applied: false, registry_version: 'abc123abc123', document_registry_version: 'abc123abc123',
        registry_version_match: true, changes: [], errors: [] })
      : ELEVATION_REQUIRED(),
    'POST /api/me/elevate': () => { elevated = true; return json({ elevated_until: new Date(Date.now() + 600_000).toISOString() }) },
  }, { elevatedUntil: null })
  wrap()
  await row('qa.agentic')
  pickFile(JSON.stringify(DOC))
  fireEvent.click(within(await screen.findByRole('dialog', { name: '重新驗證身分' })).getByRole('button', { name: '取消' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(sent(m, 'POST /api/admin/flags/import')).toHaveLength(0)
  expect(screen.queryByRole('status')).not.toBeInTheDocument()

  pickFile(JSON.stringify(DOC))
  await reverify('匯入功能旗標')
  const preview = await screen.findByRole('dialog', { name: '匯入預覽' })
  expect(within(preview).getByRole('button', { name: '沒有變更' })).toBeDisabled()
  expect(sent(m, 'POST /api/admin/flags/import')).toHaveLength(1)
})

test('匯入：不是匯出檔就不送出', async () => {
  const m = stub(OPERATOR)
  wrap()
  await row('qa.agentic')
  pickFile('{"hello": 1}', 'x.json')
  const status = await screen.findByRole('status')
  expect(status).toHaveTextContent('這不是功能旗標的匯出檔')
  expect(status).toHaveAttribute('data-tone', 'bad')
  expect(sent(m, 'POST /api/admin/flags/import')).toHaveLength(0)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})
