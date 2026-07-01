import { describe, expect, test, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { theme } from '../../../theme'
import { ConversationSidebar } from './ConversationSidebar'
import styles from './AskPage.module.css'

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
  test('空清單顯示空狀態、不渲染任何對話項目', () => {
    wrap({ conversations: [] })
    expect(screen.getByText('尚無歷史對話')).toBeInTheDocument()
    expect(screen.queryByTestId('ask-hist-item')).not.toBeInTheDocument()
  })
  test('目前作用中的對話項目帶有 active 樣式，其餘沒有', () => {
    const other = { conversation_id: 'c2', title: '問題二', last_at: null, turn_count: 1 }
    wrap({ conversations: [items[0], other], activeId: 'c2' })
    const [item1, item2] = screen.getAllByTestId('ask-hist-item')
    expect(item1).not.toHaveClass(styles.active)
    expect(item2).toHaveClass(styles.active)
  })
})
