import { describe, expect, test, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { theme } from '../../../theme'
import { ConversationSidebar } from './ConversationSidebar'

const items = [{ conversation_id: 'c1', title: '問題一', last_at: null, turn_count: 1 }]
const wrap = (props: Partial<Parameters<typeof ConversationSidebar>[0]> = {}) =>
  render(
    <MantineProvider theme={theme}>
      <ConversationSidebar conversations={items} activeId={null} onOpen={vi.fn()} onDelete={vi.fn()} onNew={vi.fn()} {...props} />
    </MantineProvider>,
  )

describe('ConversationSidebar', () => {
  test('渲染清單、開啟呼叫 onOpen', () => {
    const onOpen = vi.fn()
    wrap({ onOpen })
    fireEvent.click(screen.getByTestId('ask-hist-open'))
    expect(onOpen).toHaveBeenCalledWith('c1')
  })
  test('刪除需確認後才 onDelete', () => {
    const onDelete = vi.fn()
    wrap({ onDelete })
    fireEvent.click(screen.getByTestId('ask-hist-del'))
    // ConfirmDialog 開啟 → 點確認
    fireEvent.click(screen.getByRole('button', { name: '刪除' }))
    expect(onDelete).toHaveBeenCalledWith('c1')
  })
  test('新對話呼叫 onNew', () => {
    const onNew = vi.fn()
    wrap({ onNew })
    fireEvent.click(screen.getByTestId('ask-new'))
    expect(onNew).toHaveBeenCalled()
  })
})
