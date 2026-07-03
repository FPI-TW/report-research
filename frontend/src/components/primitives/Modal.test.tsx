import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Modal } from './Modal'

describe('Modal', () => {
  it('open=false 不渲染內容', () => {
    render(<Modal open={false} onClose={() => {}}>內容</Modal>)
    expect(screen.queryByText('內容')).toBeNull()
  })
  it('open=true 渲染 dialog + title + 內容', () => {
    render(<Modal open onClose={() => {}} title="標題">內容</Modal>)
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.getByText('標題')).toBeTruthy()
    expect(screen.getByText('內容')).toBeTruthy()
  })
  it('Esc 觸發 onClose', () => {
    const onClose = vi.fn()
    render(<Modal open onClose={onClose}>x</Modal>)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
  it('點遮罩關閉、點面板不關閉', () => {
    const onClose = vi.fn()
    const { container } = render(<Modal open onClose={onClose} title="t">body</Modal>)
    fireEvent.click(screen.getByText('body'))
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.click(container.querySelector('[data-scrim]')!)
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
