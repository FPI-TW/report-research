import { render, screen, fireEvent, act } from '@testing-library/react'
import { describe, expect, test, beforeEach, afterEach, vi } from 'vitest'
import { ComposerTools } from './ComposerTools'
import { setWebSearch } from '../../lib/useWebSearch'

// 網搜暫停（WEB_SEARCH_PAUSED，DeepSeek 遷移 PR-W）以可切換的 getter 模擬：預設走「恢復後」的行為，
// 讓開關本身的測試在暫停期間繼續守著接回點；暫停中的行為另成一組，把旗標設成 true。
const paused = vi.hoisted(() => ({ value: false }))
vi.mock('../../lib/useWebSearch', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/useWebSearch')>()
  return { ...mod, get WEB_SEARCH_PAUSED() { return paused.value } }
})

beforeEach(() => { paused.value = false; setWebSearch(false) })
afterEach(() => { paused.value = false; setWebSearch(false); localStorage.clear() })

const trigger = () => screen.getByRole('button', { name: '工具' })
const chip = () => screen.queryByRole('button', { name: '關閉網路搜尋' })

test('選單預設收合，點觸發鈕才展開', () => {
  render(<ComposerTools />)
  expect(trigger()).toHaveAttribute('aria-expanded', 'false')
  expect(screen.queryByRole('menuitemcheckbox', { name: /網路搜尋/ })).toBeNull()
  fireEvent.click(trigger())
  expect(trigger()).toHaveAttribute('aria-expanded', 'true')
  expect(screen.getByRole('menuitemcheckbox', { name: /網路搜尋/ })).toBeTruthy()
})

test('選單內切換網路搜尋 → aria-checked 反轉', () => {
  render(<ComposerTools />)
  fireEvent.click(trigger())
  const item = screen.getByRole('menuitemcheckbox', { name: /網路搜尋/ })
  expect(item).toHaveAttribute('aria-checked', 'false')
  fireEvent.click(item)
  expect(screen.getByRole('menuitemcheckbox', { name: /網路搜尋/ })).toHaveAttribute('aria-checked', 'true')
})

// 收進選單之後最容易失守的一條：工具開著卻只有展開才看得見。網搜會改變答案的
// 資料來源，使用者必須在按送出之前就知道自己開著。
test('已開啟的工具在收合狀態下以膠囊留在版面上', () => {
  render(<ComposerTools />)
  expect(chip()).toBeNull()
  act(() => setWebSearch(true))
  expect(chip()).toBeTruthy()
  expect(chip()).toHaveAttribute('aria-pressed', 'true')
  // 觸發鈕保持中性：狀態由膠囊承載，兩者都上金會讀成兩個開著的東西。
  expect(trigger()).toHaveAccessibleName('工具')
})

test('膠囊本身就是關閉鈕（不必再展開選單）', () => {
  render(<ComposerTools />)
  act(() => setWebSearch(true))
  fireEvent.click(chip()!)
  expect(chip()).toBeNull()
})

test('膠囊排在「＋」右邊，中間隔一道分隔線', () => {
  const { container } = render(<ComposerTools />)
  act(() => setWebSearch(true))
  const kids = Array.from(container.firstElementChild!.children)
  expect(kids[0]).toBe(trigger())
  expect(kids[1]).toHaveClass(/divider/)
  expect(kids[2]).toBe(chip())
})

// 說明文字已移除（選單收成單行列）。網搜的代價與資料來源提醒仍在輸入框下方那行
// 免責文案裡（Composer.test.tsx 有守），這裡確認選單本身不再重複一次。
test('選單只列名稱與開關，不再帶說明段', () => {
  render(<ComposerTools />)
  fireEvent.click(trigger())
  expect(screen.queryByText(/速度較慢/)).toBeNull()
  expect(screen.queryByText(/非受信任行情來源/)).toBeNull()
  expect(screen.getByRole('menuitemcheckbox', { name: '網路搜尋' })).toBeTruthy()
})

test('Escape 關閉選單', () => {
  render(<ComposerTools />)
  fireEvent.click(trigger())
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(trigger()).toHaveAttribute('aria-expanded', 'false')
})

test('多個實例共享開關狀態（中央與底部 Composer 同時存在）', () => {
  render(<><ComposerTools /><ComposerTools /></>)
  fireEvent.click(screen.getAllByRole('button', { name: '工具' })[0])
  fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /網路搜尋/ }))
  expect(screen.getAllByRole('button', { name: '關閉網路搜尋' })).toHaveLength(2)
})

describe('網搜暫停中（WEB_SEARCH_PAUSED）', () => {
  beforeEach(() => { paused.value = true })

  test('工具清單不含網路搜尋：沒有任何工具時整個不渲染（不留一顆點開是空選單的「＋」）', () => {
    const { container } = render(<ComposerTools />)
    expect(container.firstChild).toBeNull()
    expect(screen.queryByRole('button', { name: '工具' })).toBeNull()
    expect(screen.queryByRole('menuitemcheckbox', { name: /網路搜尋/ })).toBeNull()
  })

  test('localStorage 殘留的開啟狀態不會以膠囊外露', () => {
    setWebSearch(true)
    render(<ComposerTools />)
    expect(chip()).toBeNull()
  })
})
