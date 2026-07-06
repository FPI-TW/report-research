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

test('引言 > 渲染為 blockquote，不外洩 > 字面', () => {
  const { container } = render(<div>{renderAnswer('> 這是引言\n> 第二行', 0, () => {})}</div>)
  const bq = container.querySelector('blockquote')
  expect(bq).toBeTruthy()
  expect(bq?.textContent).toContain('這是引言')
  expect(container.textContent).not.toContain('>')
})

test('GFM 表格渲染為 table，含表頭與資料列，不外洩 | 字面', () => {
  const md = '| 券商 | 評等 |\n| --- | --- |\n| 元大 | 買進 |\n| 凱基 | 中立 |'
  const { container } = render(<div>{renderAnswer(md, 0, () => {})}</div>)
  expect(container.querySelector('table')).toBeTruthy()
  expect(container.querySelectorAll('th')).toHaveLength(2)
  expect(container.querySelectorAll('tbody tr')).toHaveLength(2)
  expect(container.querySelectorAll('td')).toHaveLength(4)
  expect(container.textContent).not.toContain('|')
})

test('表格儲存格內 [n] 仍為可點膠囊', () => {
  const onCite = vi.fn()
  const md = '| 標的 | 來源 |\n| --- | --- |\n| 台積電 | [1] |'
  render(<div>{renderAnswer(md, 3, onCite)}</div>)
  fireEvent.click(screen.getByRole('button', { name: '1' }))
  expect(onCite).toHaveBeenCalledWith(1)
})

test('缺分隔列的 | a | b | 不誤判為表格', () => {
  const { container } = render(<div>{renderAnswer('比較 | A | B | 三者', 0, () => {})}</div>)
  expect(container.querySelector('table')).toBeNull()
  expect(container.textContent).toContain('| A | B |')
})
