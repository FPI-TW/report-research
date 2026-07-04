import { render, screen, fireEvent } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { renderAnswer } from './askMarkdown'

test('[n] 界內渲染金膠囊並可點；界外保留字面', () => {
  const onCite = vi.fn()
  render(<div>{renderAnswer('看 [1] 與 [9] 的比較', 3, onCite)}</div>)
  const pill = screen.getByRole('button', { name: '1' })
  fireEvent.click(pill)
  expect(onCite).toHaveBeenCalledWith(1)
  expect(screen.getByText(/\[9\]/)).toBeInTheDocument() // 界外字面
})

test('標題與清單成塊', () => {
  const { container } = render(<div>{renderAnswer('# 標題\n- 甲\n- 乙', 0, () => {})}</div>)
  expect(container.querySelector('h3, h4')).toBeTruthy()
  expect(container.querySelectorAll('li')).toHaveLength(2)
})

test('XSS：原始 HTML 不被解讀為標籤', () => {
  const { container } = render(<div>{renderAnswer('<img src=x onerror=alert(1)> 純文字', 0, () => {})}</div>)
  expect(container.querySelector('img')).toBeNull()
  expect(container.textContent).toContain('<img')
})

test('連結僅接受 http(s)，javascript: 退回字面', () => {
  const { container } = render(<div>{renderAnswer('[好](https://a.com) [壞](javascript:alert(1))', 0, () => {})}</div>)
  const a = container.querySelector('a')
  expect(a?.getAttribute('href')).toBe('https://a.com')
  expect(a).toHaveAttribute('rel', 'noopener noreferrer')
  expect(container.textContent).toContain('[壞]')
})
