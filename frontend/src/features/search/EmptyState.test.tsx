import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { EmptyState } from './EmptyState'

describe('EmptyState', () => {
  it('search：清除篩選再試 + 瀏覽全部報告 皆顯示且回呼', () => {
    const onClear = vi.fn(); const onBrowseAll = vi.fn()
    render(<EmptyState copy={{ title: '找不到「x」的相關研報', hint: 'h', showClear: true, showBrowseAll: true }}
      onClear={onClear} onBrowseAll={onBrowseAll} />)
    fireEvent.click(screen.getByRole('button', { name: '清除篩選再試' }))
    fireEvent.click(screen.getByRole('button', { name: '瀏覽全部報告' }))
    expect(onClear).toHaveBeenCalled(); expect(onBrowseAll).toHaveBeenCalled()
  })
  it('browse 無篩選：無任何 CTA', () => {
    render(<EmptyState copy={{ title: '沒有符合條件的研報', hint: 'h', showClear: false, showBrowseAll: false }}
      onClear={() => {}} onBrowseAll={() => {}} />)
    expect(screen.queryByRole('button')).toBeNull()
  })
})
