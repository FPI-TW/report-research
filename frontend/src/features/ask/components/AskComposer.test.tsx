import { describe, expect, test, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { theme } from '../../../theme'
import { AskComposer } from './AskComposer'

const wrap = (props: Parameters<typeof AskComposer>[0]) =>
  render(
    <MantineProvider theme={theme}>
      <AskComposer {...props} />
    </MantineProvider>,
  )

describe('AskComposer', () => {
  test('Enter 送出（去頭尾空白）', () => {
    const onSend = vi.fn()
    wrap({ onSend })
    const ta = screen.getByTestId('ask-input')
    fireEvent.change(ta, { target: { value: '  你好  ' } })
    fireEvent.keyDown(ta, { key: 'Enter' })
    expect(onSend).toHaveBeenCalledWith('你好')
  })
  test('Shift+Enter 不送出', () => {
    const onSend = vi.fn()
    wrap({ onSend })
    const ta = screen.getByTestId('ask-input')
    fireEvent.change(ta, { target: { value: 'x' } })
    fireEvent.keyDown(ta, { key: 'Enter', shiftKey: true })
    expect(onSend).not.toHaveBeenCalled()
  })
  test('空白不送出', () => {
    const onSend = vi.fn()
    wrap({ onSend })
    fireEvent.click(screen.getByTestId('ask-send'))
    expect(onSend).not.toHaveBeenCalled()
  })

  test('IME 組字中 Enter（isComposing=true）不送出', () => {
    const onSend = vi.fn()
    wrap({ onSend })
    const ta = screen.getByTestId('ask-input')
    fireEvent.change(ta, { target: { value: '你好' } })
    fireEvent.keyDown(ta, { key: 'Enter', isComposing: true })
    expect(onSend).not.toHaveBeenCalled()
  })

  test('IME 組字結束後一般 Enter 正常送出', () => {
    const onSend = vi.fn()
    wrap({ onSend })
    const ta = screen.getByTestId('ask-input')
    fireEvent.change(ta, { target: { value: '你好' } })
    // 組字中不送
    fireEvent.keyDown(ta, { key: 'Enter', isComposing: true })
    expect(onSend).not.toHaveBeenCalled()
    // 組字結束後正常送
    fireEvent.keyDown(ta, { key: 'Enter', isComposing: false })
    expect(onSend).toHaveBeenCalledWith('你好')
  })
})
