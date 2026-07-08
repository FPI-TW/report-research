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

test('表格儲存格內跳脫管線 \\| 視為字面，不誤切欄', () => {
  const md = '| 運算 | 說明 |\n| --- | --- |\n| a \\| b | 位元或 |'
  const { container } = render(<div>{renderAnswer(md, 0, () => {})}</div>)
  const cells = container.querySelectorAll('tbody td')
  expect(cells).toHaveLength(2) // 兩欄，不因 \| 多切一欄
  expect(cells[0].textContent).toContain('a | b') // \| 還原為字面 |
})

test('資料列欄數與表頭不符時對齊表頭欄數（多截少補）', () => {
  const md = '| 標的 | 評等 |\n| --- | --- |\n| 台積 | 買進 | 多一欄 |\n| 只有一欄 |'
  const { container } = render(<div>{renderAnswer(md, 0, () => {})}</div>)
  const trs = container.querySelectorAll('tbody tr')
  expect(trs).toHaveLength(2)
  expect(trs[0].querySelectorAll('td')).toHaveLength(2) // 多的第三欄被截掉
  expect(trs[1].querySelectorAll('td')).toHaveLength(2) // 缺的欄補空
  expect(trs[1].querySelectorAll('td')[1].textContent).toBe('') // 第二欄為空
  expect(container.textContent).not.toContain('多一欄') // 溢出欄不顯示
})

test('相鄰兩表格（中間無空行）各自成表，不互相吞併', () => {
  const md = '| A | B |\n| --- | --- |\n| 1 | 2 |\n| C | D |\n| --- | --- |\n| 3 | 4 |'
  const { container } = render(<div>{renderAnswer(md, 0, () => {})}</div>)
  expect(container.querySelectorAll('table')).toHaveLength(2)
  // 第一表僅一列資料，第二表的表頭/分隔列不被吞成第一表的爛列
  const tables = container.querySelectorAll('table')
  expect(tables[0].querySelectorAll('tbody tr')).toHaveLength(1)
  expect(tables[1].querySelectorAll('th')).toHaveLength(2)
  expect(container.textContent).not.toContain('---')
})
