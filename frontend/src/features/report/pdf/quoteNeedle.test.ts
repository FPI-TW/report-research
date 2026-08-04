import { describe, expect, it } from 'vitest'
import { needleLadder } from './quoteNeedle'

describe('needleLadder', () => {
  it('短引文只有一階（沒有可回退的東西就不要憑空造）', () => {
    expect(needleLadder('視 NYPCB 為首選')).toEqual(['視 NYPCB 為首選'])
  })

  // 2.0% 的真實 quote 內含換行（_clean_quote 刻意保留內部空白），
  // 而 PDF 頁面上那裡是一個普通空白。12 份 PDF 實測 11/12 直接搜會落空。
  it('換行與連續空白折成單一空白，且那是第一階', () => {
    expect(needleLadder('毛利率上修\n至五成')[0]).toBe('毛利率上修 至五成')
    expect(needleLadder('第三季  營收\t創高')[0]).toBe('第三季 營收 創高')
  })

  it('去頭尾空白', () => {
    expect(needleLadder('  台積電先進封裝  ')[0]).toBe('台積電先進封裝')
  })

  // 這一條釘死的是「絕不可移除所有空白」：PDFium 只在相鄰字元有一側是 CJK 時
  // 才跳過頁面空白，ASCII 之間的空白必須如實出現。移除＝整批英文引文落空。
  it('純英文引文的字間空白必須原樣保留', () => {
    const ladder = needleLadder('TV panel prices dropped sharply')
    expect(ladder[0]).toBe('TV panel prices dropped sharply')
    expect(ladder.every(n => !/^\S+$/.test(n) || n.length < 32)).toBe(true)
    // 不得出現任何把空白全清掉的候選
    expect(ladder).not.toContain('TVpanelpricesdroppedsharply')
  })

  it('中英混排同樣保留 ASCII 之間的空白', () => {
    expect(needleLadder('高於 Fed 2% 的長期目標')[0]).toBe('高於 Fed 2% 的長期目標')
  })

  it('第二階是最長的無空白片段（引文跨越 PDF 斷行時的回退）', () => {
    // 「台積電第三季營收創歷史新高」比其他片段長
    expect(needleLadder('根據 台積電第三季營收創歷史新高 的說法')[1]).toBe('台積電第三季營收創歷史新高')
  })

  it('最長片段太短就不進階梯（會 match 到滿篇都是的字串）', () => {
    // 片段 'a' / 'b' / 'c' 皆短於門檻
    expect(needleLadder('a b c')).toEqual(['a b c'])
  })

  it('長引文的第三階是前 24 字元', () => {
    const long = '一二三四五六七八九十' + '壹貳參肆伍陸柒捌玖拾' + '甲乙丙丁戊己'
    const ladder = needleLadder(long)
    expect(ladder[ladder.length - 1]).toBe(long.slice(0, 24))
    expect(ladder[ladder.length - 1]).toHaveLength(24)
  })

  it('候選重複時去重（短引文的前綴等於自己）', () => {
    const ladder = needleLadder('台積電先進封裝產能')
    expect(new Set(ladder).size).toBe(ladder.length)
  })

  it('空白或空字串回空陣列（呼叫端據此不發搜尋）', () => {
    expect(needleLadder('')).toEqual([])
    expect(needleLadder('   \n  ')).toEqual([])
  })
})
