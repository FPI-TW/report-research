import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { UserMessage } from './UserMessage'

describe('UserMessage', () => {
  it('renders text', () => {
    render(<UserMessage text="你好" onEdit={() => {}} />)
    expect(screen.getByText('你好')).toBeInTheDocument()
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
