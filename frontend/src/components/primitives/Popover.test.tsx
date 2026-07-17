import { useRef } from 'react'
import { fireEvent, render, screen, waitForElementToBeRemoved } from '@testing-library/react'
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

test('open=true 面板以 role 呈現', () => {
  render(<Harness open onClose={() => {}} />)
  expect(screen.getByRole('menu')).toBeInTheDocument()
})

test('open→false 後離場卸載（面板移除）', async () => {
  const { rerender } = render(<Harness open onClose={() => {}} />)
  expect(screen.getByText('登出')).toBeInTheDocument()
  rerender(<Harness open={false} onClose={() => {}} />)
  await waitForElementToBeRemoved(() => screen.queryByText('登出'))
})
