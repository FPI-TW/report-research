import { render, screen, fireEvent } from '@testing-library/react'
import { useState } from 'react'
import { expect, test, vi } from 'vitest'
import { Composer } from './Composer'

function Harness({ onSubmit }: { onSubmit: (q: string) => void }) {
  const [v, setV] = useState('')
  return <Composer value={v} onChange={setV} onSubmit={onSubmit} />
}
const area = () => screen.getByPlaceholderText('輸入你的問題…')

test('Enter 送出 trim 後值；Shift+Enter 不送', () => {
  const onSubmit = vi.fn()
  render(<Harness onSubmit={onSubmit} />)
  fireEvent.change(area(), { target: { value: '  台積電評價  ' } })
  fireEvent.keyDown(area(), { key: 'Enter', shiftKey: true })
  expect(onSubmit).not.toHaveBeenCalled()
  fireEvent.keyDown(area(), { key: 'Enter' })
  expect(onSubmit).toHaveBeenCalledWith('台積電評價')
})

test('IME 組字中的 Enter 不送出', () => {
  const onSubmit = vi.fn()
  render(<Harness onSubmit={onSubmit} />)
  fireEvent.change(area(), { target: { value: '注音' } })
  fireEvent.keyDown(area(), { key: 'Enter', isComposing: true })
  expect(onSubmit).not.toHaveBeenCalled()
})

test('disabled 時不顯示可用送出鈕（改停止鈕）', () => {
  const onSubmit = vi.fn()
  render(<Composer value="x" onChange={() => {}} onSubmit={onSubmit} disabled />)
  expect(screen.queryByRole('button', { name: '送出' })).toBeNull()
  expect(onSubmit).not.toHaveBeenCalled()
})

test('shows stop button when busy and calls onStop', async () => {
  const onStop = vi.fn()
  render(<Composer value="" onChange={() => {}} onSubmit={() => {}} disabled onStop={onStop} />)
  const btn = screen.getByRole('button', { name: /停止/ })
  fireEvent.click(btn)
  expect(onStop).toHaveBeenCalledOnce()
})
