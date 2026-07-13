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
