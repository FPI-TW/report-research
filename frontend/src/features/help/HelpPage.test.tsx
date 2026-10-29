import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { copyText } from '../../lib/clipboard'
import { MARKET_ORDER, marketLabel } from '../../lib/meta'
import { setFeatures } from '../../lib/useFeatures'
import HelpPage from './HelpPage'
import { HELP_TOC } from './helpData'

vi.mock('../../lib/clipboard', () => ({ copyText: vi.fn(() => Promise.resolve()) }))

// 頁面層會 useFeatures() 去抓 /api/features：測試不連網，一律回「沒有覆寫」，旗標由 setFeatures 決定。
beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))
})
afterEach(() => {
  vi.unstubAllGlobals()
  setFeatures(null)
})

function renderHelp() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/help']}>
        <HelpPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const pageText = () => document.body.textContent ?? ''

test('目錄依序列出九節，捷徑卡連到改名後的新網址', () => {
  renderHelp()
  expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('使用說明')
  const toc = screen.getByRole('navigation', { name: '說明目錄' })
  expect(within(toc).getAllByRole('button').map(b => b.textContent)).toEqual(HELP_TOC.map(t => t.label))
  for (const t of HELP_TOC) expect(document.getElementById(`help-${t.id}`)).not.toBeNull()

  const hrefs = Object.fromEntries(
    ['研報庫', '研究助理', '券商共識', '每日簡報'].map(name => [name, screen.getByRole('link', { name: new RegExp(`^${name}`) }).getAttribute('href')]),
  )
  expect(hrefs).toEqual({ 研報庫: '/library', 研究助理: '/assistant', 券商共識: '/consensus', 每日簡報: '/brief' })
})

test('點目錄只捲說明頁自己的捲動區、標成目前一節，焦點移到該節標題', () => {
  const scrollTo = vi.fn()
  const intoView = vi.spyOn(Element.prototype, 'scrollIntoView')
  Element.prototype.scrollTo = scrollTo as unknown as typeof Element.prototype.scrollTo
  try {
    renderHelp()
    const toc = screen.getByRole('navigation', { name: '說明目錄' })
    const row = within(toc).getByRole('button', { name: '券商共識' })
    fireEvent.click(row)
    expect(row).toHaveAttribute('aria-current', 'location')
    expect(within(toc).getByRole('button', { name: '開始使用' })).not.toHaveAttribute('aria-current')
    expect(scrollTo).toHaveBeenCalledTimes(1)
    // 交接包坑 1：scrollIntoView 會連外層一起捲
    expect(intoView).not.toHaveBeenCalled()
    expect(document.activeElement).toBe(screen.getByRole('heading', { level: 2, name: '券商共識' }))
  } finally {
    // @ts-expect-error 還原 jsdom 原本沒有的方法
    delete Element.prototype.scrollTo
    intoView.mockRestore()
  }
})

test('頁名與說明一律用改名後的名稱，不殘留舊名與已移除的動線', () => {
  renderHelp()
  const text = pageText()
  for (const old of ['券商觀點', '觀點雷達', '智能問答', '檢索／問答', '導入監控', '彈出視窗', '在原文中尋找']) {
    expect(text, old).not.toContain(old)
  }
  // 產品 UI 規則：不用「·」當分隔符
  expect(text).not.toContain('·')
})

test('閱讀頁一節說明點摘錄會在 PDF 標出原文、縮圖上有金色編號', () => {
  renderHelp()
  const sec = document.getElementById('help-reader')!
  expect(sec.textContent).toMatch(/點重點摘錄，PDF 會捲到原文並標出位置/)
  expect(sec.textContent).toMatch(/縮圖右上的金色編號/)
  expect(sec.textContent).toContain('1–5')
})

test('五段操作示範都是有說明文字的圖', () => {
  renderHelp()
  const demos = screen.getAllByRole('img', { name: /^操作示範：/ })
  expect(demos).toHaveLength(5)
  const owners = demos.map(d => d.closest('section')?.id)
  expect(owners).toEqual(['help-library', 'help-reader', 'help-assistant', 'help-consensus', 'help-brief'])
  // 每日簡報示範的是在月曆點 27 日
  expect(demos[4]).toHaveAccessibleName(/月曆點選 27 日/)
})

test('市場代碼表列出全部九個 findb 市場代碼', () => {
  renderHelp()
  const table = within(document.getElementById('help-markets')!).getByRole('table')
  const rows = within(table).getAllByRole('row').slice(1)
  expect(rows.map(r => within(r).getAllByRole('cell')[0].textContent)).toEqual([...MARKET_ORDER])
  expect(rows.map(r => within(r).getAllByRole('cell')[1].textContent)).toEqual(MARKET_ORDER.map(marketLabel))
})

test('快捷鍵表涵蓋全站、研報庫、研究助理與閱讀頁，非 Mac 顯示 Ctrl', () => {
  renderHelp()
  const table = within(document.getElementById('help-keys')!).getByRole('table')
  const rows = within(table).getAllByRole('row').slice(1).map(r => r.textContent)
  for (const expected of [
    '開啟搜尋全站CtrlK',
    '新對話全站CtrlShiftO',
    '關閉彈窗或選單全站Esc',
    '聚焦研報搜尋框研報庫/',
    '送出提問研究助理Enter',
    '換行研究助理ShiftEnter',
    '在 PDF 中搜尋閱讀頁CtrlF',
    '翻頁閱讀頁PageUpPageDown',
    '跳到首頁或末頁閱讀頁HomeEnd',
    '下一個搜尋結果閱讀頁Enter',
    '上一個搜尋結果閱讀頁ShiftEnter',
  ]) {
    expect(rows).toContain(expected)
  }
})

// DeepSeek 條款要求向終端使用者揭露 AI 生成（遷移 PR-U）：說明頁要列出生成的功能與資料去向。
test('說明頁揭露 AI 生成內容與資料送往模型供應商', () => {
  renderHelp()
  expect(within(screen.getByRole('navigation', { name: '說明目錄' })).getByRole('button', { name: 'AI 生成內容' })).toBeInTheDocument()
  const section = document.getElementById('help-ai')!
  for (const word of ['研究助理', '摘要', '標題', '重點摘錄', '標註', '券商共識', '每日簡報', '可能有誤']) {
    expect(section.textContent).toContain(word)
  }
  // 既有語料多半是遷移前由 Claude 產出：供應商寫成「目前為」，不說全部出自 DeepSeek
  expect(section.textContent).toMatch(/大型語言模型（目前為 DeepSeek）自動生成/)
  expect(section.textContent).toMatch(/提問、回答與相關的研報內容會送往模型供應商（目前為 DeepSeek）處理/)
})

// 網搜由功能旗標 ask.web_search 控制（還沒抓到或沒開＝暫停，輸入框不列「＋」裡的網搜）：說明不能教一顆看不到的開關。
test('網路搜尋的說明跟著功能旗標：暫停時不提，開啟時才說明', () => {
  renderHelp()
  expect(pageText()).not.toContain('網路搜尋')
  act(() => setFeatures({ 'ask.web_search': true }))
  expect(document.getElementById('help-assistant')!.textContent).toContain('輸入框的「＋」可以開啟網路搜尋')
  expect(document.getElementById('help-ai')!.textContent).toContain('開啟網路搜尋時')
})

test('平台網址取目前站台，複製後按鈕換成已複製', async () => {
  renderHelp()
  expect(screen.getByLabelText('平台網址')).toHaveValue(window.location.origin)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '複製' })) })
  expect(copyText).toHaveBeenCalledWith(window.location.origin)
  expect(screen.getByRole('button', { name: '已複製' })).toBeInTheDocument()
})
