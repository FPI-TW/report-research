import { describe, expect, it } from 'vitest'
import html from '../index.html?raw'
import favicon from '../public/favicon.svg?raw'

describe('應用程式 favicon 契約', () => {
  it('HTML 以根路徑宣告 favicon 供 Vite 套用 /app base，且圖檔存在並可辨識為 SVG', () => {
    expect(html).toContain('<link rel="icon" type="image/svg+xml" href="/favicon.svg" />')
    expect(favicon).toMatch(/<svg[\s>]/)
    expect(favicon).toContain('viewBox="0 0 64 64"')
  })
})
