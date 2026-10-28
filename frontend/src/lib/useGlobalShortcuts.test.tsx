import { fireEvent, render } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { useGlobalShortcuts } from './useGlobalShortcuts'

function Harness({ pathname = '/library', toggleSearch = vi.fn(), newChat = vi.fn() }) {
  useGlobalShortcuts({ toggleSearch, newChat, pathname })
  return <input aria-label="搜尋研報" />
}

test('⌘／Ctrl K 開關搜尋；⌘／Ctrl Shift O 新對話', () => {
  const toggleSearch = vi.fn()
  const newChat = vi.fn()
  render(<Harness toggleSearch={toggleSearch} newChat={newChat} />)
  fireEvent.keyDown(document, { key: 'k', ctrlKey: true })
  fireEvent.keyDown(document, { key: 'K', metaKey: true })
  expect(toggleSearch).toHaveBeenCalledTimes(2)
  fireEvent.keyDown(document, { key: 'O', ctrlKey: true, shiftKey: true })
  expect(newChat).toHaveBeenCalledTimes(1)
})

test('研報庫按 / 聚焦搜尋框；別頁或打字中不攔', () => {
  const { getByLabelText, unmount } = render(<Harness pathname="/library" />)
  fireEvent.keyDown(document.body, { key: '/' })
  expect(getByLabelText('搜尋研報')).toHaveFocus()
  unmount()
  const other = render(<Harness pathname="/brief" />)
  ;(document.activeElement as HTMLElement | null)?.blur()
  fireEvent.keyDown(document.body, { key: '/' })
  expect(other.getByLabelText('搜尋研報')).not.toHaveFocus()
})

test('輸入法組字中不處理', () => {
  const toggleSearch = vi.fn()
  render(<Harness toggleSearch={toggleSearch} />)
  fireEvent.keyDown(document, { key: 'k', ctrlKey: true, isComposing: true })
  expect(toggleSearch).not.toHaveBeenCalled()
})
