import { describe, expect, it } from 'vitest'
import { sourceFromPath } from './backNav'

describe('sourceFromPath', () => {
  it('新網址（含 basename /app）對到來源頁', () => {
    expect(sourceFromPath('/app/library')).toBe('library')
    expect(sourceFromPath('/app/assistant')).toBe('assistant')
    expect(sourceFromPath('/app/consensus')).toBe('consensus')
    expect(sourceFromPath('/app/brief')).toBe('brief')
    expect(sourceFromPath('/app/help')).toBe('help')
    expect(sourceFromPath('/app')).toBe('library')
  })

  // 轉址前的舊網址還會留在瀏覽紀錄裡
  it('舊網址一併認得', () => {
    expect(sourceFromPath('/app/search')).toBe('library')
    expect(sourceFromPath('/app/ask')).toBe('assistant')
    expect(sourceFromPath('/app/radar')).toBe('consensus')
  })

  it('不帶 basename 也行（MemoryRouter、站內路徑）', () => {
    expect(sourceFromPath('/consensus')).toBe('consensus')
  })

  it('另一篇研報或不認得的頁 → null（返回鈕退回「上一頁」）', () => {
    expect(sourceFromPath(`/app/report/${'a'.repeat(64)}`)).toBeNull()
    expect(sourceFromPath('/app/admin')).toBeNull()
    // 前綴相同但不是那一頁
    expect(sourceFromPath('/application')).toBeNull()
  })
})
