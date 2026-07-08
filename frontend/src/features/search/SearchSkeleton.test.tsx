import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SearchSkeleton } from './SearchSkeleton'

describe('SearchSkeleton', () => {
  it('卡片檢視渲染 aria-hidden 骨架容器與預設 6 張卡片骨架', () => {
    render(<SearchSkeleton view="cards" mode="search" />)
    expect(screen.getByTestId('search-skeleton').getAttribute('aria-hidden')).toBe('true')
    expect(screen.getAllByTestId('skeleton-card')).toHaveLength(6)
  })

  it('表格檢視（search）欄位數為 8', () => {
    render(<SearchSkeleton view="table" mode="search" />)
    expect(document.querySelectorAll('thead th')).toHaveLength(8)
  })

  it('表格檢視（browse）欄位數為 6', () => {
    render(<SearchSkeleton view="table" mode="browse" />)
    expect(document.querySelectorAll('thead th')).toHaveLength(6)
  })
})
