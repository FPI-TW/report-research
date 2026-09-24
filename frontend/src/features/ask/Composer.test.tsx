import { render, screen, fireEvent, act } from '@testing-library/react'
import { useState } from 'react'
import { expect, test, vi, afterEach } from 'vitest'
import { AI_NOTE, Composer } from './Composer'
import { setWebSearch } from '../../lib/useWebSearch'

function Harness({ onSubmit }: { onSubmit: (q: string) => void }) {
  const [v, setV] = useState('')
  return <Composer value={v} onChange={setV} onSubmit={onSubmit} />
}
const area = () => screen.getByPlaceholderText('輸入你的問題…')

test('Enter 送出 trim 後值；Shift+Enter 不送', () => {
  const onSubmit = vi.fn()
  render(<Harness onSubmit={onSubmit} />)
  fireEvent.change(area(), { target: { value: '  台積電評價  ' } })
  fireEvent.keyDown(area(), { key: 'Enter', shiftKey: true })
  expect(onSubmit).not.toHaveBeenCalled()
  fireEvent.keyDown(area(), { key: 'Enter' })
  expect(onSubmit).toHaveBeenCalledWith('台積電評價')
})

test('IME 組字中的 Enter 不送出', () => {
  const onSubmit = vi.fn()
  render(<Harness onSubmit={onSubmit} />)
  fireEvent.change(area(), { target: { value: '注音' } })
  fireEvent.keyDown(area(), { key: 'Enter', isComposing: true })
  expect(onSubmit).not.toHaveBeenCalled()
})

test('disabled 時不顯示可用送出鈕（改停止鈕）', () => {
  const onSubmit = vi.fn()
  render(<Composer value="x" onChange={() => {}} onSubmit={onSubmit} disabled />)
  expect(screen.queryByRole('button', { name: '送出' })).toBeNull()
  expect(onSubmit).not.toHaveBeenCalled()
})

test('shows stop button when busy and calls onStop', async () => {
  const onStop = vi.fn()
  render(<Composer value="" onChange={() => {}} onSubmit={() => {}} disabled onStop={onStop} />)
  const btn = screen.getByRole('button', { name: /停止/ })
  fireEvent.click(btn)
  expect(onStop).toHaveBeenCalledOnce()
})

// ── 工具選單（M11）────────────────────────────────────────────────────────
// 開關本身的行為在 ComposerTools.test.tsx；這裡只驗 Composer 這一層的接線：
// 工具鈕在最左邊、免責文案隨開關切換。

test('工具鈕排在輸入框之前（版面最左）', () => {
  setWebSearch(false)
  const { container } = render(<Composer value="" onChange={() => {}} onSubmit={() => {}} />)
  const box = container.querySelector('textarea')!.parentElement!
  // 工具鈕自帶一層定位用的 wrapper（Popover 需要 relative 脈絡），所以比對的是
  // 「第一個子節點裡有工具鈕」而不是節點本身。
  expect(box.children[0].contains(screen.getByRole('button', { name: '工具' }))).toBe(true)
  expect(box.children[1].tagName).toBe('TEXTAREA')
})

test('底部變體的免責文案隨開關切換（開啟後點明網路資訊非受信任行情來源）', () => {
  setWebSearch(false)
  render(<Composer value="" onChange={() => {}} onSubmit={() => {}} />)
  expect(screen.queryByText(/非受信任行情來源/)).toBeNull()
  act(() => setWebSearch(true))
  expect(screen.getByText(/非受信任行情來源/)).toBeTruthy()
  setWebSearch(false)
})

// AI 生成揭露（DeepSeek 遷移 PR-U）：條款要求標示 AI 生成。空狀態的中央輸入框是第一題送出前
// 唯一看得到的那個，所以兩個變體都要有。
test.each(['center', 'bottom'] as const)('%s 變體揭露回答由 AI（DeepSeek）生成、可能有誤', (variant) => {
  setWebSearch(false)
  render(<Composer value="" onChange={() => {}} onSubmit={() => {}} variant={variant} />)
  expect(screen.getByText(AI_NOTE)).toBeInTheDocument()
  expect(AI_NOTE).toMatch(/AI（DeepSeek）/)
  expect(AI_NOTE).toMatch(/可能有誤/)
  expect(AI_NOTE).toMatch(/原始研報/)
})

// ── 換行後的版面（M11）──────────────────────────────────────────────────────
// jsdom 的 scrollHeight 恆為 0，所以量測型邏輯只能靠 stub 驗。這段守的是兩件事：
// 換行時控制項退到第二列（否則文字被擠在「＋」與膠囊右邊那條窄欄，愈打愈窄），
// 以及沒到高度上限時不畫捲軸（22.4px 的行盒四捨五入就足以讓它冒出來）。

function stubScrollHeight(px: number) {
  return vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(px)
}
afterEach(() => { vi.restoreAllMocks() })

const boxOf = (c: HTMLElement) => c.querySelector('textarea')!.parentElement!

test('單行時維持一列版面', () => {
  stubScrollHeight(32)  // 一行：行盒 22.4 ＋ 上下內距
  const { container } = render(<Composer value="短" onChange={() => {}} onSubmit={() => {}} />)
  expect(boxOf(container)).not.toHaveClass(/multiline/)
})

test('換行後控制項退到第二列', () => {
  stubScrollHeight(55)  // 兩行
  const { container } = render(<Composer value="很長的一段" onChange={() => {}} onSubmit={() => {}} />)
  expect(boxOf(container)).toHaveClass(/multiline/)
})

test('未達高度上限時關掉捲軸，達到才開', () => {
  stubScrollHeight(55)
  const { container, rerender } = render(<Composer value="兩行" onChange={() => {}} onSubmit={() => {}} />)
  expect(container.querySelector('textarea')!.style.overflowY).toBe('hidden')
  vi.restoreAllMocks()
  stubScrollHeight(400)  // 遠超過 MAX_INPUT_HEIGHT
  rerender(<Composer value="非常長" onChange={() => {}} onSubmit={() => {}} />)
  expect(container.querySelector('textarea')!.style.overflowY).toBe('auto')
})
