import { describe, it, expect } from 'vitest'
import { displayTitle } from './displayTitle'

describe('displayTitle', () => {
  it('有標題就顯示標題（檔名多是券商流水號，對讀者沒有意義）', () => {
    expect(displayTitle({ title: '記憶體報價反彈', file_name: '6247269925_260728_gs_umt.pdf' }))
      .toBe('記憶體報價反彈')
  })
  it('缺標題回退檔名——批次是漸進補的，缺值是常態不是錯誤', () => {
    expect(displayTitle({ title: null, file_name: 'a.pdf' })).toBe('a.pdf')
    expect(displayTitle({ file_name: 'a.pdf' })).toBe('a.pdf')
  })
  it('空白標題視同沒有標題（不可讓卡片標題變成空白列）', () => {
    expect(displayTitle({ title: '   ', file_name: 'a.pdf' })).toBe('a.pdf')
  })
  it('兩者皆缺才用預設字樣，且可覆寫', () => {
    expect(displayTitle({})).toBe('報告')
    expect(displayTitle(null)).toBe('報告')
    expect(displayTitle({ title: null, file_name: null }, '')).toBe('')
  })
})
