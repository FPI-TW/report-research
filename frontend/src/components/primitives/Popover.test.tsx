import { useRef } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { Popover } from './Popover'

function Harness({ open, onClose }: { open: boolean; onClose: () => void }) {
  const anchor = useRef<HTMLDivElement>(null)
  return (
    <div ref={anchor}>
      <Popover open={open} onClose={onClose}>
        <button>登出</button>
      </Popover>
    </div>
  )
}

test('open=false 不渲染內容', () => {
  render(<Harness open={false} onClose={() => {}} />)
  expect(screen.queryByText('登出')).not.toBeInTheDocument()
})

test('open=true 渲染內容；Esc 觸發 onClose', () => {
  const onClose = vi.fn()
  render(<Harness open onClose={onClose} />)
  expect(screen.getByText('登出')).toBeInTheDocument()
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(onClose).toHaveBeenCalled()
})

test('open=true 面板帶 data-state=open', () => {
  render(<Harness open onClose={() => {}} />)
  const panel = screen.getByText('登出').closest('[data-state]')
  expect(panel?.getAttribute('data-state')).toBe('open')
})
