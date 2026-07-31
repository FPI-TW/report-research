import { FontCharset } from '@embedpdf/models'
import { expect, it } from 'vitest'
import { CJK_FONT_FALLBACK } from './fontFallback'
// `?raw` 取原始碼字串：不會執行 PdfViewer，也就不會把 PDFium 與全部外掛拉進模組圖。
// 刻意不用 node:fs —— 本專案沒有裝 @types/node（`tsc --noEmit` 只涵蓋 src），
// 為了一支測試把它裝進來會連帶改變整個瀏覽器專案的全域型別（例如 setTimeout 的回傳型別）。
import pdfViewerSource from './PdfViewer.tsx?raw'

/**
 * 這支測試守的是一種**完全靜默**的退化。
 *
 * 引擎端的實際邏輯是 `fontFallback === null ? undefined : fontFallback ?? cdnFontConfig`，
 * 所以只要我們傳的值變成 undefined（設定被刪、匯入被改壞、wiring 被拿掉），引擎就會
 * 自動改用 jsDelivr CDN——不會拋錯、不會有 console 訊息，唯一症狀是「在擋外連的網路下
 * 中文變成空白方框」，而那要有人剛好開到一份沒內嵌字型的研報才看得見。
 */

const variants = CJK_FONT_FALLBACK.fonts[FontCharset.CHINESEBIG5]

it('繁中字符集有備援字型設定（缺了就會靜默退回 CDN）', () => {
  expect(CJK_FONT_FALLBACK).not.toBeNull()
  expect(Array.isArray(variants)).toBe(true)
  expect(variants).not.toHaveLength(0)
})

it('字型一律自架，設定裡不得出現任何外部主機', () => {
  const urls = (variants as { url: string }[]).map(v => v.url)
  for (const url of urls) {
    expect(url).toBeTruthy()
    // `://` 就是外連（http/https 皆然）；自架的一律是以 / 開頭的同源絕對路徑
    expect(url).not.toContain('://')
  }
  // baseUrl 只在 url 非絕對路徑時才會被前綴；設了外部 baseUrl 等於繞過上面那條檢查
  expect(CJK_FONT_FALLBACK.baseUrl ?? '').not.toContain('://')
})

it('至少涵蓋正常與粗體兩個字重（其餘字重靠最接近比對落到這兩個）', () => {
  const weights = (variants as { weight?: number }[]).map(v => v.weight)
  expect(weights).toContain(400)
  expect(weights).toContain(700)
})

// 設定本身正確、但沒有接到引擎上，症狀與「設定不存在」一模一樣（都是靜默走 CDN）。
// 這條刻意用靜態檢查而非渲染 PdfViewer：後者會把 PDFium 與全部外掛拉進模組圖，
// 而那份負載正是先前壓垮 App.test.tsx 的原因。
it('PdfViewer 確實把設定傳給 usePdfiumEngine', () => {
  expect(pdfViewerSource).toContain('CJK_FONT_FALLBACK')
  expect(pdfViewerSource).toMatch(/fontFallback:\s*CJK_FONT_FALLBACK/)
})
