import { describe, expect, it } from 'vitest'

/*
 * 全站 CSS 慣例守門（跨 feature，故不放在任何一個 *CssContracts 裡）。
 *
 * 存在理由是一個**只在 production build 出現**的缺陷：CSS Modules 的壓縮器把
 * `backdrop-filter` 與 `-webkit-backdrop-filter` 當成同一個屬性的兩份宣告，只留**最後**
 * 一份。原始碼四處都寫成「標準在前、前綴在後」，於是 dist 裡只剩 `-webkit-` 那份——
 * 而 Chrome 149 已經移除 `-webkit-backdrop-filter` 別名（`CSS.supports` 對它回 false）。
 * 結果是全站每一片磨砂玻璃在正式站上都沒有模糊：側欄、提問輸入列、監控頁表頭、
 * 浮層、PDF 工具列、觀點雷達的吸頂列。
 *
 * 兩件事讓它活了很久：dev server 不壓縮，兩份宣告都在，**開發時看起來完全正常**；
 * 而 `@supports` 那條退場路徑也救不了——瀏覽器確實支援標準屬性，只是 dist 裡沒有它。
 * 症狀最嚴重的地方是雷達標的頁：券商表格捲到吸頂列底下，72% 白底沒有模糊，
 * 目標價數字直接壓在麵包屑與標的名上（2026-08-07 回報）。
 */
const cssFiles = import.meta.glob('../**/*.css', {
  eager: true,
  import: 'default',
  query: '?raw',
}) as Record<string, string>

function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, '')
}

/** 最內層的 `{...}` ＝ 一條規則的宣告串。順序只在同一條規則內有意義——跨規則比對會把
 *  「規則 A 以標準屬性收尾、規則 B 以前綴屬性開頭」誤判成違規（實測會誤報兩個檔）。 */
function ruleBodies(css: string): string[] {
  return [...css.matchAll(/\{([^{}]*)\}/g)].map(m => m[1])
}

describe('CSS 慣例', () => {
  it('掃得到全站的 CSS（反向自保：glob 失效時下面兩條會變成空守門）', () => {
    expect(Object.keys(cssFiles).length).toBeGreaterThan(20)
  })

  /* 同一族的第二個壓縮器陷阱（2026-08-20 實測）：靠重複類名提高特異度的寫法
     （`.panel.panel { ... }`）會被壓縮器折回單一 `.panel`，特異度一起消失。
     dev server 不壓縮 ⇒ 覆寫看起來正常；正式站則退回「誰在後面誰贏」的注入順序，
     而注入順序等於 import 順序，那不是任何人宣告過的契約。
     要跨 module 覆寫請改用自訂屬性（一邊給值、一邊給 var() fallback），
     見 primitives/Modal.module.css 的 `--tf-modal-width`。 */
  it('不得用重複類名（.a.a）提高特異度——壓縮器會折掉', () => {
    const offenders: string[] = []
    for (const [path, raw] of Object.entries(cssFiles)) {
      const noComments = stripComments(raw)
      for (const m of noComments.matchAll(/(\.[A-Za-z_][\w-]*)\1(?![\w-])/g)) {
        offenders.push(`${path}: ${m[0]}`)
      }
    }
    expect(
      offenders,
      '重複類名在壓縮後會折回單一類名，特異度隨之消失 ⇒ 覆寫只在 dev 有效。'
      + '改用自訂屬性做跨 module 覆寫。',
    ).toEqual([])
  })

  it.each([
    ['backdrop-filter', '-webkit-backdrop-filter'],
  ])('%s 一律排在 %s **後面**（壓縮器只留最後一份）', (standard, prefixed) => {
    const offenders: string[] = []
    for (const [path, raw] of Object.entries(cssFiles)) {
      for (const body of ruleBodies(stripComments(raw))) {
        // 只看宣告（行首或 ; 之後），不看 @supports 的條件式——那裡兩種拼法都該出現。
        const decl = new RegExp(`(?:^|;)\\s*(-webkit-)?${standard}\\s*:`, 'g')
        const order = [...body.matchAll(decl)].map(m => (m[1] ? prefixed : standard))
        // 同一條規則裡，標準屬性後面還跟著前綴版本 ⇒ 壓縮後標準那份會被丟掉
        if (order.indexOf(standard) !== -1
          && order.lastIndexOf(prefixed) > order.indexOf(standard)) {
          offenders.push(path)
          break
        }
      }
    }
    expect(
      offenders,
      `這些檔把 ${standard} 寫在 ${prefixed} 前面；壓縮後只會留下前綴版本，`
      + '而 Chrome 149 起不再認得它 ⇒ 效果在正式站上整個消失（dev server 看不出來）。',
    ).toEqual([])
  })

  it('每一處 -webkit-backdrop-filter 都有對應的標準宣告', () => {
    const missing: string[] = []
    for (const [path, raw] of Object.entries(cssFiles)) {
      const css = stripComments(raw)
      const prefixed = (css.match(/[;{]\s*-webkit-backdrop-filter\s*:/g) ?? []).length
      const standard = (css.match(/[;{]\s*backdrop-filter\s*:/g) ?? []).length
      if (prefixed > standard) missing.push(`${path}（前綴 ${prefixed} / 標準 ${standard}）`)
    }
    expect(missing).toEqual([])
  })
})
