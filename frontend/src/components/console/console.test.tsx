import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  Button, CheckRow, ConfirmDialog, Dialog, DialogActions, DialogBody, Drawer, DrawerSection, FieldGroup, FormDialog, Field,
  PageHeader, RowActions, Segmented, StatTile, Tabs, Toaster,
  TrendBars, dismissToast, isElevationCancelled, toast, TOAST_BAD_MS, TOAST_MS, useElevate, useQueryParam,
} from './index'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  dismissToast()
})

describe('PageHeader', () => {
  it('kicker 與標題預設取導覽結構；長說明收在「說明」後面', () => {
    render(
      <MemoryRouter initialEntries={['/admin/operations/services']}>
        <PageHeader summary="34 個服務，33 個正常。" help="排程類服務在兩次執行之間會顯示閒置。" actions={<Button>重新整理</Button>} />
      </MemoryRouter>,
    )
    expect(screen.getByText('維運 / 健康')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: '服務' })).toBeInTheDocument()
    expect(screen.queryByText(/兩次執行之間/)).not.toBeInTheDocument()
    const toggle = screen.getByRole('button', { name: '說明' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText(/兩次執行之間/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新整理' })).toBeInTheDocument()
  })

  it('沒有分組的領域 kicker 只有領域；不在導覽裡的頁用自己給的標題', () => {
    render(<MemoryRouter initialEntries={['/admin/users']}><PageHeader summary="x" /></MemoryRouter>)
    expect(screen.getByText('存取')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: '帳號管理' })).toBeInTheDocument()
  })
})

describe('RowActions', () => {
  const setup = (rowClick = vi.fn()) => {
    const run = { reset: vi.fn(), role: vi.fn(), disable: vi.fn(), remove: vi.fn() }
    render(
      <table><tbody><tr onClick={rowClick}><td>
        <RowActions label="jason.wu 的更多操作"
          primary={{ label: '重設密碼', onClick: run.reset }}
          more={[
            { label: '刪除帳號', onClick: run.remove, danger: true },
            { label: '改角色', onClick: run.role },
            false,
            { label: '停用', onClick: run.disable, danger: true, disabled: true, hint: '不能停用自己' },
          ]} />
      </td></tr></tbody></table>,
    )
    return { run, rowClick }
  }

  it('一顆主要按鈕＋「更多」；危險項排最後、上方有分隔線；點擊不冒泡到列', () => {
    const { run, rowClick } = setup()
    fireEvent.click(screen.getByRole('button', { name: '重設密碼' }))
    expect(run.reset).toHaveBeenCalledTimes(1)
    const more = screen.getByRole('button', { name: 'jason.wu 的更多操作' })
    expect(more).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(more)
    const menu = within(screen.getByRole('menu'))
    expect(menu.getAllByRole('menuitem').map(b => b.textContent)).toEqual(['改角色', '刪除帳號', '停用'])
    const sep = menu.getByRole('separator')
    expect(sep.nextElementSibling).toHaveTextContent('刪除帳號')
    fireEvent.click(menu.getByRole('menuitem', { name: '改角色' }))
    expect(run.role).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(rowClick).not.toHaveBeenCalled()
  })

  it('停用的項目有 title 說明原因，點了沒反應；Esc 關選單並把焦點還給按鈕', () => {
    const { run } = setup()
    const more = screen.getByRole('button', { name: 'jason.wu 的更多操作' })
    fireEvent.click(more)
    const disabled = screen.getByRole('menuitem', { name: '停用' })
    expect(disabled).toBeDisabled()
    expect(disabled).toHaveAttribute('title', '不能停用自己')
    fireEvent.click(disabled)
    expect(run.disable).not.toHaveBeenCalled()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(more).toHaveFocus()
  })

  it('沒有更多項目就不畫「更多」', () => {
    render(<RowActions primary={{ label: '日誌', onClick: () => {} }} more={[false, null]} />)
    expect(screen.queryByRole('button', { name: /更多/ })).not.toBeInTheDocument()
  })
})

describe('ConfirmDialog 與 FormDialog', () => {
  it('確認框是 alertdialog，焦點預設在取消；Esc 取消', () => {
    const onConfirm = vi.fn(); const onCancel = vi.fn()
    render(<ConfirmDialog open title="停用「jason.wu」？" body="停用後立即生效。" confirmLabel="停用" onConfirm={onConfirm} onCancel={onCancel} />)
    const dlg = within(screen.getByRole('alertdialog', { name: '停用「jason.wu」？' }))
    expect(dlg.getByRole('button', { name: '取消' })).toHaveFocus()
    fireEvent.click(dlg.getByRole('button', { name: '停用' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('忙碌中：兩顆按鈕停用，Esc 不關', () => {
    const onCancel = vi.fn()
    render(<ConfirmDialog open busy tone="safe" title="恢復？" body="x" confirmLabel="恢復" onConfirm={() => {}} onCancel={onCancel} />)
    expect(screen.getByRole('button', { name: '恢復' })).toBeDisabled()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onCancel).not.toHaveBeenCalled()
  })

  it('表單：焦點在第一個欄位，Enter 送出，錯誤用 role=alert，停用時不送出', () => {
    const onSubmit = vi.fn()
    function Form({ disabled }: { disabled?: boolean }) {
      const [v, setV] = useState('')
      return (
        <FormDialog open title="隱藏研報" submitLabel="隱藏" submitTone="danger" submitDisabled={disabled} error={v === 'x' ? '原因太短' : null}
          onSubmit={onSubmit} onClose={() => {}}>
          <Field label="原因" counter={`${v.length}／500`}><input value={v} onChange={e => setV(e.target.value)} /></Field>
        </FormDialog>
      )
    }
    const { rerender } = render(<Form />)
    const input = screen.getByLabelText(/原因/)
    expect(input).toHaveFocus()
    fireEvent.change(input, { target: { value: 'x' } })
    expect(screen.getByRole('alert')).toHaveTextContent('原因太短')
    expect(screen.getByText('1／500')).toBeInTheDocument()
    fireEvent.submit(input.closest('form')!)
    expect(onSubmit).toHaveBeenCalledTimes(1)
    rerender(<Form disabled />)
    fireEvent.submit(input.closest('form')!)
    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: '隱藏' })).toBeDisabled()
  })
})

function LocationProbe() {
  const l = useLocation()
  return <output data-testid="loc">{l.pathname + l.search}</output>
}

describe('Drawer 與 useQueryParam', () => {
  function Page() {
    const [service, setService] = useQueryParam('service')
    const [confirm, setConfirm] = useState(false)
    return (
      <>
        <button type="button" onClick={() => setService('web')}>開 web</button>
        <Drawer open={service !== null} onClose={() => setService(null)} title={service ?? ''} monoTitle width={470}
          footer={<Button variant="primary" onClick={() => setConfirm(true)}>重新啟動</Button>}>
          <DrawerSection title="屬性">unit</DrawerSection>
        </Drawer>
        <ConfirmDialog open={confirm} title="重新啟動 web？" body="x" confirmLabel="重新啟動" tone="safe"
          onConfirm={() => setConfirm(false)} onCancel={() => setConfirm(false)} />
        <LocationProbe />
      </>
    )
  }

  it('抽屜狀態在 URL：直接開連結就是打開的；關閉拿掉參數、保留其他參數', () => {
    render(<MemoryRouter initialEntries={['/admin/operations/services?filter=attention&service=web']}><Page /></MemoryRouter>)
    const drawer = within(screen.getByRole('dialog', { name: 'web' }))
    expect(drawer.getByText('unit')).toBeInTheDocument()
    fireEvent.click(drawer.getByRole('button', { name: '關閉' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByTestId('loc')).toHaveTextContent('/admin/operations/services?filter=attention')
    fireEvent.click(screen.getByRole('button', { name: '開 web' }))
    expect(screen.getByTestId('loc')).toHaveTextContent('filter=attention&service=web')
  })

  it('抽屜上疊著確認框時，Esc 先關確認框，再按一次才關抽屜', () => {
    render(<MemoryRouter initialEntries={['/x?service=web']}><Page /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: '重新啟動' }))
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'web' })).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

describe('toast', () => {
  it('底部顯示操作結果，2.6 秒後消失；新的蓋掉舊的', () => {
    vi.useFakeTimers()
    render(<Toaster />)
    act(() => toast('已停用「jason.wu」'))
    expect(screen.getByRole('status')).toHaveTextContent('已停用「jason.wu」')
    act(() => { vi.advanceTimersByTime(TOAST_MS - 100) })
    act(() => toast('已啟用「jason.wu」'))
    expect(screen.getByRole('status')).toHaveTextContent('已啟用「jason.wu」')
    act(() => { vi.advanceTimersByTime(TOAST_MS - 100) })
    expect(screen.getByRole('status')).toBeInTheDocument()
    act(() => { vi.advanceTimersByTime(200) })
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('失敗的訊息停久一點（多半是後端的 detail，比較長）', () => {
    vi.useFakeTimers()
    render(<Toaster />)
    act(() => toast('至少要保留一位啟用中的管理員', { tone: 'bad' }))
    expect(screen.getByRole('status')).toHaveAttribute('data-tone', 'bad')
    act(() => { vi.advanceTimersByTime(TOAST_MS + 500) })
    expect(screen.getByRole('status')).toHaveTextContent('至少要保留一位啟用中的管理員')
    act(() => { vi.advanceTimersByTime(TOAST_BAD_MS - TOAST_MS) })
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})

describe('useElevate', () => {
  function mount(me: Record<string, unknown>, action: () => Promise<string>, elevateStatus = 200) {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/me') return new Response(JSON.stringify({ id: 'me', username: 'root', role: 'admin', ...me }))
      if (url === '/api/me/elevate') {
        const body = JSON.parse(String(init?.body))
        if (elevateStatus !== 200) return new Response(JSON.stringify({ detail: '密碼或驗證碼錯誤', code: 'bad_credentials' }), { status: elevateStatus })
        if (!body.code && me.totp_enabled === undefined) {
          return new Response(JSON.stringify({ detail: '需要驗證碼', code: 'totp_required' }), { status: 403 })
        }
        return new Response(JSON.stringify({ elevated_until: new Date(Date.now() + 600_000).toISOString() }))
      }
      return new Response('{}', { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const outcomes: string[] = []
    function Page() {
      const { elevated, dialog } = useElevate()
      const go = () => elevated('調整權限', action).then(
        r => outcomes.push(r), e => outcomes.push(isElevationCancelled(e) ? 'cancelled' : `error:${(e as Error).message}`))
      return <><button type="button" onClick={() => { void go() }}>執行</button>{dialog}</>
    }
    render(<QueryClientProvider client={qc}><Page /></QueryClientProvider>)
    return { fetchMock, outcomes, qc }
  }
  const elevateCalls = (f: ReturnType<typeof vi.fn>) => f.mock.calls.filter(c => c[0] === '/api/me/elevate')

  it('還在 10 分鐘視窗內：直接執行，不詢問', async () => {
    const action = vi.fn(async () => 'done')
    const { fetchMock, outcomes } = mount({ elevated_until: new Date(Date.now() + 60_000).toISOString() }, action)
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/me', expect.anything()))
    await act(async () => { await Promise.resolve() })
    fireEvent.click(screen.getByRole('button', { name: '執行' }))
    await waitFor(() => expect(outcomes).toEqual(['done']))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(elevateCalls(fetchMock)).toHaveLength(0)
  })

  it('沒驗證過：先問密碼與 6 位數驗證碼，成功才執行，之後不再問', async () => {
    const action = vi.fn(async () => 'done')
    const { fetchMock, outcomes, qc } = mount({ totp_enabled: true }, action)
    await waitFor(() => expect(qc.getQueryData(['me'])).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: '執行' }))
    const dlg = within(await screen.findByRole('dialog', { name: '重新驗證身分' }))
    expect(dlg.getByText(/「調整權限」需要重新驗證/)).toBeInTheDocument()
    expect(action).not.toHaveBeenCalled()
    fireEvent.click(dlg.getByRole('button', { name: '驗證' }))
    expect(dlg.getByRole('alert')).toHaveTextContent('請輸入密碼')
    fireEvent.change(dlg.getByLabelText('密碼'), { target: { value: 'fixed-test-secret-pw' } })
    fireEvent.change(dlg.getByLabelText('驗證碼'), { target: { value: '12a34' } })
    expect(dlg.getByLabelText('驗證碼')).toHaveValue('1234')
    fireEvent.click(dlg.getByRole('button', { name: '驗證' }))
    expect(dlg.getByRole('alert')).toHaveTextContent('驗證碼是 6 位數字')
    fireEvent.change(dlg.getByLabelText('驗證碼'), { target: { value: '123456' } })
    fireEvent.click(dlg.getByRole('button', { name: '驗證' }))
    await waitFor(() => expect(outcomes).toEqual(['done']))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(JSON.parse(String(elevateCalls(fetchMock)[0][1].body))).toEqual({ password: 'fixed-test-secret-pw', code: '123456' })
    expect((qc.getQueryData(['me']) as { elevated_until?: string }).elevated_until).toBeTruthy()
    // 第二次：視窗內，不再詢問
    fireEvent.click(screen.getByRole('button', { name: '執行' }))
    await waitFor(() => expect(outcomes).toEqual(['done', 'done']))
    expect(elevateCalls(fetchMock)).toHaveLength(1)
  })

  it('取消：動作不執行，呼叫端拿到可辨識的取消', async () => {
    const action = vi.fn(async () => 'done')
    const { outcomes, qc } = mount({ totp_enabled: false }, action)
    await waitFor(() => expect(qc.getQueryData(['me'])).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: '執行' }))
    const dlg = within(await screen.findByRole('dialog', { name: '重新驗證身分' }))
    expect(dlg.queryByLabelText('驗證碼')).not.toBeInTheDocument()
    fireEvent.click(dlg.getByRole('button', { name: '取消' }))
    await waitFor(() => expect(outcomes).toEqual(['cancelled']))
    expect(action).not.toHaveBeenCalled()
  })

  it('驗證失敗：顯示後端訊息，對話框留著', async () => {
    const { outcomes, qc } = mount({ totp_enabled: false }, async () => 'done', 403)
    await waitFor(() => expect(qc.getQueryData(['me'])).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: '執行' }))
    const dlg = within(await screen.findByRole('dialog', { name: '重新驗證身分' }))
    fireEvent.change(dlg.getByLabelText('密碼'), { target: { value: 'fixed-test-secret-bad' } })
    fireEvent.click(dlg.getByRole('button', { name: '驗證' }))
    expect(await dlg.findByRole('alert')).toHaveTextContent('密碼或驗證碼錯誤')
    expect(outcomes).toEqual([])
  })
})

describe('小元件', () => {
  it('Segmented 與 Tabs 標出目前選到的那一個，帶數量', () => {
    const onSeg = vi.fn(); const onTab = vi.fn()
    render(
      <>
        <Segmented label="篩選" value="all" onChange={onSeg} options={[{ value: 'all', label: '全部', count: 34 }, { value: 'attention', label: '需要注意', count: 1 }]} />
        <Tabs label="分類" value="draft" onChange={onTab} tabs={[{ value: 'processing', label: '處理中' }, { value: 'draft', label: '待審草稿', count: 3 }]} />
      </>,
    )
    expect(screen.getByRole('button', { name: /全部/ })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: /需要注意/ }))
    expect(onSeg).toHaveBeenCalledWith('attention')
    expect(screen.getByRole('tab', { name: /待審草稿/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /待審草稿/ })).toHaveTextContent('3')
    fireEvent.click(screen.getByRole('tab', { name: '處理中' }))
    expect(onTab).toHaveBeenCalledWith('processing')
  })

  it('TrendBars：超過門檻的柱子標 warn，最後一根可標金色，null 留空位', () => {
    const { container } = render(
      <TrendBars label="磁碟使用率" max={100} threshold={85} highlightLast format={v => `${v}%`}
        bars={[{ value: 40, label: '13 時' }, { value: null, label: '14 時' }, { value: 90, label: '15 時' }, { value: 50, label: '16 時' }]} />,
    )
    const bars = [...container.querySelectorAll('[title]')].filter(el => el.getAttribute('title') !== '門檻 85%')
    expect(bars.map(b => b.getAttribute('title'))).toEqual(['13 時・40%', '14 時・沒有資料', '15 時・90%', '16 時・50%'])
    expect(bars[1].children).toHaveLength(0)
    expect(bars[2].firstElementChild).toHaveAttribute('data-tone', 'warn')
    expect(bars[3].firstElementChild).toHaveAttribute('data-tone', 'gold')
    expect(container.querySelector('[title="門檻 85%"]')).not.toBeNull()
  })

  it('RowActions 的主要動作給 to 就是真的連結', () => {
    render(<MemoryRouter><RowActions primary={{ label: '日誌', to: '/admin/operations/logs?service=web' }} /></MemoryRouter>)
    expect(screen.getByRole('link', { name: '日誌' })).toHaveAttribute('href', '/admin/operations/logs?service=web')
  })

  it('Segmented 停用時每一顆都不能按', () => {
    const onChange = vi.fn()
    render(<Segmented label="狀態" value="open" onChange={onChange} disabled wrap options={[{ value: 'open', label: '待處理' }, { value: 'done', label: '已處理' }]} />)
    expect(screen.getByRole('button', { name: '已處理' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '已處理' }))
    expect(onChange).not.toHaveBeenCalled()
  })

  it('StatTile 給 name 就是具名的群組；補充文字的完整內容在 title', () => {
    render(<StatTile name="今日超額" label="今日超額" value={3} unit="次" sub="alice、bob…" subTitle="alice、bob、carol" />)
    const tile = within(screen.getByRole('group', { name: '今日超額' }))
    expect(tile.getByText('3')).toBeInTheDocument()
    expect(tile.getByText('alice、bob…')).toHaveAttribute('title', 'alice、bob、carol')
  })

  it('FieldGroup 不是 label：點標題不會誤觸裡面的控制項；CheckRow 回報勾選狀態', () => {
    const onCheck = vi.fn(); const onSeg = vi.fn()
    render(
      <FieldGroup label="角色" hint="調整後對方的下一個動作就生效">
        <Segmented label="角色選項" value="user" onChange={onSeg} options={[{ value: 'user', label: '一般使用者' }, { value: 'admin', label: '管理員' }]} />
        <CheckRow checked={false} onChange={onCheck}>查看問答內容</CheckRow>
        <CheckRow checked disabled onChange={onCheck}>super admin 已含</CheckRow>
      </FieldGroup>,
    )
    const group = screen.getByRole('group', { name: '角色' })
    expect(group.tagName).toBe('DIV')
    expect(group.querySelector('label > [role="group"]')).toBeNull()
    fireEvent.click(within(group).getByText('角色'))
    expect(onSeg).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('checkbox', { name: '查看問答內容' }))
    expect(onCheck).toHaveBeenCalledWith(true)
    expect(screen.getByRole('checkbox', { name: 'super admin 已含' })).toBeDisabled()
  })

  it('Dialog 設 dismissible={false}：Esc 與點遮罩都不關，只能按裡面的按鈕', () => {
    const onClose = vi.fn()
    render(
      <Dialog open title="新金鑰" width={500} dismissible={false} onClose={onClose}>
        <DialogBody>這把金鑰只會顯示這一次。</DialogBody>
        <DialogActions><Button variant="primary" onClick={onClose}>我已複製，關閉</Button></DialogActions>
      </Dialog>,
    )
    fireEvent.keyDown(document, { key: 'Escape' })
    fireEvent.mouseDown(screen.getByRole('dialog', { name: '新金鑰' }).parentElement!)
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '我已複製，關閉' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
