import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { highlight } from './highlight'

describe('highlight', () => {
  it('命中詞包 <mark>、文字內容不變', () => {
    const { container } = render(<div>{highlight('台積電營收成長', ['台積', '積電'])}</div>)
    expect(container.querySelectorAll('mark').length).toBeGreaterThan(0)
    expect(container.textContent).toBe('台積電營收成長')
  })
  it('無 terms → 純文字、無 mark', () => {
    const { container } = render(<div>{highlight('純文字', [])}</div>)
    expect(container.querySelectorAll('mark').length).toBe(0)
    expect(container.textContent).toBe('純文字')
  })
  it('不解讀 HTML 特殊字元（XSS 安全）', () => {
    const { container } = render(<div>{highlight('<img src=x onerror=1>', ['img'])}</div>)
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('<img')
  })
})
