import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { UserMessage } from './UserMessage'

describe('UserMessage', () => {
  it('renders text', () => {
    render(<UserMessage text="你好" onEdit={() => {}} />)
    expect(screen.getByText('你好')).toBeInTheDocument()
  })

  // 動作列預設隱形、hover 才浮現（jsdom 模擬不了 :hover，故只驗得到「預設是隱形」這一半；
  // vitest 設了 css:true，CSS Modules 的規則會真的套進 jsdom，opacity 才量得到）。
  it('action row is hidden until hover', () => {
    render(<UserMessage text="你好" onEdit={() => {}} />)
    const actions = screen.getByRole('button', { name: '編輯' }).parentElement!
    expect(getComputedStyle(actions).opacity).toBe('0')
  })

  it('copy button writes the question to the clipboard', async () => {
    // userEvent.setup() 會替換 navigator.clipboard 為可讀回的 stub（jsdom 本身沒有）。
    // **還得補 isSecureContext**：jsdom 裡它是 undefined，而 lib/clipboard 的
    // copyText 以「有 clipboard **且** 是安全情境」決定走原生還是 execCommand 後備
    // ——不補的話這裡會落到後備，而 jsdom 連 document.execCommand 都沒有。
    // 這裡模擬的是真實瀏覽器在 HTTPS/localhost 下的情形；後備那條由
    // src/lib/clipboard.test.ts 專門覆蓋。
    Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true })
    const user = userEvent.setup()
    render(<UserMessage text="台積電先進封裝" onEdit={() => {}} />)
    await user.click(screen.getByRole('button', { name: '複製提問' }))
    await expect(navigator.clipboard.readText()).resolves.toBe('台積電先進封裝')
  })

  it('edit mode submits new text', async () => {
    const onEdit = vi.fn()
    render(<UserMessage text="舊問題" onEdit={onEdit} />)
    await userEvent.click(screen.getByRole('button', { name: /編輯/ }))
    const box = screen.getByRole('textbox')
    await userEvent.clear(box)
    await userEvent.type(box, '新問題')
    await userEvent.click(screen.getByRole('button', { name: /送出/ }))
    expect(onEdit).toHaveBeenCalledWith('新問題')
  })

  it('cancel exits edit mode without calling onEdit', async () => {
    const onEdit = vi.fn()
    render(<UserMessage text="舊問題" onEdit={onEdit} />)
    await userEvent.click(screen.getByRole('button', { name: /編輯/ }))
    await userEvent.click(screen.getByRole('button', { name: /取消/ }))
    expect(onEdit).not.toHaveBeenCalled()
    expect(screen.getByText('舊問題')).toBeInTheDocument()
  })
})

// ── 編輯框比照 ChatGPT（按鈕包進輸入框容器、不可手動 resize）──────────────
it('編輯框：按鈕與輸入框包在同一個容器，textarea 無框且不可手動 resize', async () => {
  render(<UserMessage text="舊問題" onEdit={() => {}} />)
  await userEvent.click(screen.getByRole('button', { name: '編輯' }))
  const ta = screen.getByLabelText('編輯提問')
  // 不給使用者拖大小；外框畫在容器上，textarea 自己是裸的
  expect(getComputedStyle(ta).resize).toBe('none')
  expect(getComputedStyle(ta).borderTopStyle).toBe('none')
  // 送出鈕（在 editActions 列內）與 textarea 同屬同一個 editBox 容器
  const send = screen.getByRole('button', { name: '送出' })
  expect(send.parentElement!.parentElement).toBe(ta.parentElement)
})

it('編輯框：Enter 送出、Shift+Enter 不送出、Escape 取消', async () => {
  const onEdit = vi.fn()
  render(<UserMessage text="舊問題" onEdit={onEdit} />)
  await userEvent.click(screen.getByRole('button', { name: '編輯' }))
  const ta = screen.getByLabelText('編輯提問')
  await userEvent.clear(ta)
  await userEvent.type(ta, '新問題')
  await userEvent.keyboard('{Shift>}{Enter}{/Shift}')
  expect(onEdit).not.toHaveBeenCalled()
  await userEvent.keyboard('{Enter}')
  expect(onEdit).toHaveBeenCalledWith('新問題')

  // Escape：重開編輯後按 Esc 應退回氣泡顯示、不送出
  await userEvent.click(screen.getByRole('button', { name: '編輯' }))
  await userEvent.keyboard('{Escape}')
  expect(screen.queryByLabelText('編輯提問')).toBeNull()
  expect(screen.getByText('舊問題')).toBeInTheDocument()
  expect(onEdit).toHaveBeenCalledTimes(1)
})
