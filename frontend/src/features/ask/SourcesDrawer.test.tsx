import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, expect, onTestFinished, test, vi } from 'vitest'
import { SourcesDrawer } from './SourcesDrawer'
import type { AnswerView } from '../../lib/askReducer'

function view(over: Partial<AnswerView> = {}): AnswerView {
  return {
    answer: '',
    sources: [
      { n: 1, report_id: 'r1', file_name: '台積電.pdf', market: 'TW', report_date: '2026-06-20', is_latest: false },
      { n: 2, report_id: 'r2', file_name: '聯發科.pdf', title: '聯發科展望', market: 'TW', report_date: '2026-06-18', is_latest: false },
    ],
    extSources: [{ title: '外部新聞', url: 'https://news.example.com/a' }],
    qaId: 'qa1', feedback: null, stopped: false,
    ...over,
  }
}

const base = { active: null, nonce: 0, onClose: () => {}, onOpenReport: async () => {} }

afterEach(() => { vi.restoreAllMocks() })

test('研報與網路補充排成同一份清單，編號沿用回答裡的 [n]；計數是全部來源數', () => {
  render(<SourcesDrawer open view={view()} {...base} />)
  const panel = screen.getByRole('complementary', { name: '引用來源' })
  expect(panel).toHaveTextContent('3')
  const cards = panel.querySelectorAll('article')
  expect(Array.from(cards, c => c.getAttribute('data-n'))).toEqual(['1', '2', '3'])
  expect(screen.getByRole('heading', { name: '聯發科展望' })).toBeInTheDocument()  // title 優先於檔名
  // 版面上不用「·」當分隔符
  expect(panel.textContent).not.toContain('·')
})

test('「在閱讀頁開啟」把該篇的 report_id 交給呼叫端', async () => {
  const onOpenReport = vi.fn(async () => {})
  render(<SourcesDrawer open view={view()} {...base} onOpenReport={onOpenReport} />)
  fireEvent.click(screen.getAllByRole('button', { name: /在閱讀頁開啟/ })[1])
  await waitFor(() => expect(onOpenReport).toHaveBeenCalledWith('r2'))
})

test('網路補充連到原網頁、另開分頁、不帶 referrer', () => {
  render(<SourcesDrawer open view={view()} {...base} />)
  const ext = screen.getByRole('link', { name: /開啟網頁/ })
  expect(ext).toHaveAttribute('href', 'https://news.example.com/a')
  expect(ext).toHaveAttribute('target', '_blank')
  expect(ext).toHaveAttribute('rel', 'noopener noreferrer')
})

test('指定的那一則閃金框，只捲抽屜本身（不用 scrollIntoView）', () => {
  // 裝上探針（jsdom 的元素沒有 scrollTo；scrollIntoView 是 test/setup.ts 補的空函式），測完還原。
  const intoView = vi.fn()
  const scrollTo = vi.fn()
  const orig = { intoView: Element.prototype.scrollIntoView, scrollTo: HTMLElement.prototype.scrollTo }
  Element.prototype.scrollIntoView = intoView
  HTMLElement.prototype.scrollTo = scrollTo as unknown as typeof HTMLElement.prototype.scrollTo
  onTestFinished(() => {
    Element.prototype.scrollIntoView = orig.intoView
    HTMLElement.prototype.scrollTo = orig.scrollTo
  })
  // 抽屜捲動區 0–300，第 3 則落在 400–480：要往下捲到讓它整張露出來
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    if (this.tagName === 'ARTICLE' && this.dataset.n === '3') return { top: 400, bottom: 480, left: 0, right: 0, width: 0, height: 80, x: 0, y: 0, toJSON: () => ({}) }
    return { top: 0, bottom: 300, left: 0, right: 0, width: 0, height: 300, x: 0, y: 0, toJSON: () => ({}) }
  })
  render(<SourcesDrawer open view={view()} {...base} active={3} nonce={1} />)
  const card = screen.getByRole('complementary').querySelector('article[data-n="3"]')!
  expect(card.className).toMatch(/flashA/)
  expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ top: 188 }))
  expect(intoView).not.toHaveBeenCalled()
})

test('同一則再點一次（nonce 換了）閃框 class 交替，動畫才會重播', () => {
  const { rerender } = render(<SourcesDrawer open view={view()} {...base} active={1} nonce={1} />)
  const card = () => screen.getByRole('complementary').querySelector('article[data-n="1"]')!
  expect(card().className).toMatch(/flashA/)
  rerender(<SourcesDrawer open view={view()} {...base} active={1} nonce={2} />)
  expect(card().className).toMatch(/flashB/)
})

test('Esc 關閉；更上層的浮層已處理掉的 Esc（defaultPrevented）不關', () => {
  const onClose = vi.fn()
  render(<SourcesDrawer open view={view()} {...base} onClose={onClose} />)
  const prevented = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
  prevented.preventDefault()
  document.dispatchEvent(prevented)
  expect(onClose).not.toHaveBeenCalled()
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(onClose).toHaveBeenCalledOnce()
})

test('關閉時留在 DOM（滑出動畫要用）但 inert、對輔助技術隱藏', () => {
  render(<SourcesDrawer open={false} view={view()} {...base} />)
  expect(screen.queryByRole('complementary', { name: '引用來源' })).toBeNull()
  const panel = document.querySelector('aside')!
  expect(panel).toHaveAttribute('aria-hidden', 'true')
  expect(panel).toHaveAttribute('inert')
})
