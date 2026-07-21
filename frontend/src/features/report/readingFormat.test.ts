import { describe, it, expect } from 'vitest'
import type { Takeaway } from '../../lib/readingSchemas'
import {
  buildTextSegments,
  isJumpable,
  isValidHash,
  matchedPct,
  matchedText,
  metaParts,
  reportHref,
  similarMeta,
  stanceDisplay,
  stanceTone,
  targetSub,
  fmtTargetPrice,
} from './readingFormat'

const HASH = 'a'.repeat(64)

function tk(p: Partial<Takeaway>): Takeaway {
  return { ordinal: 1, claim: '主張', quote: null, quote_start: null, quote_end: null, anchor_method: null, ...p }
}

describe('isValidHash', () => {
  it('64-hex 通過（大小寫皆可）', () => {
    expect(isValidHash(HASH)).toBe(true)
    expect(isValidHash('A'.repeat(64))).toBe(true)
  })
  it('長度不符或含非 hex 字元一律擋下', () => {
    expect(isValidHash('a'.repeat(63))).toBe(false)
    expect(isValidHash('a'.repeat(65))).toBe(false)
    expect(isValidHash('g'.repeat(64))).toBe(false)
    expect(isValidHash('')).toBe(false)
    expect(isValidHash(null)).toBe(false)
    expect(isValidHash(undefined)).toBe(false)
  })
})

describe('reportHref', () => {
  it('無 chunk 時不帶 query', () => {
    expect(reportHref(HASH)).toBe(`/report/${HASH}`)
    expect(reportHref(HASH, null)).toBe(`/report/${HASH}`)
  })
  it('有 chunk 時帶 ?chunk=N（含 0）', () => {
    expect(reportHref(HASH, 7)).toBe(`/report/${HASH}?chunk=7`)
    expect(reportHref(HASH, 0)).toBe(`/report/${HASH}?chunk=0`)
  })
})

describe('metaParts', () => {
  it('source_display 優先於 source', () => {
    expect(metaParts({ source: 'daiwa', source_display: '大和', report_date: '2026-07-14' }))
      .toEqual(['大和', '2026-07-14'])
  })
  it('source 為 null 時不留多餘分隔', () => {
    expect(metaParts({ source: null, source_display: null, report_date: '2026-07-14' }))
      .toEqual(['2026-07-14'])
  })
  it('report_date 為 null 時不留多餘分隔', () => {
    expect(metaParts({ source: 'daiwa', source_display: null, report_date: null })).toEqual(['daiwa'])
  })
  it('兩者皆 null 時為空陣列（報頭不渲染任何分隔點）', () => {
    expect(metaParts({ source: null, source_display: null, report_date: null })).toEqual([])
  })
  it('日期只取 YYYY-MM-DD', () => {
    expect(metaParts({ source: null, source_display: null, report_date: '2026-07-14T08:00:00Z' }))
      .toEqual(['2026-07-14'])
  })
})

describe('similarMeta', () => {
  it('缺券商或日期時不留下懸空的分隔點', () => {
    expect(similarMeta({ source_display: '凱基', report_date: '2026-07-08' })).toBe('凱基 · 2026-07-08')
    expect(similarMeta({ source: null, report_date: '2026-07-08' })).toBe('2026-07-08')
    expect(similarMeta({ source_display: '凱基', report_date: null })).toBe('凱基')
    expect(similarMeta({ source: null, report_date: null })).toBe('')
  })
})

describe('matchedText / matchedPct', () => {
  it('設計稿拍板說法「9/12 段相符」', () => {
    expect(matchedText(9, 12)).toBe('9/12 段相符')
  })
  it('百分比四捨五入且夾在 0–100', () => {
    expect(matchedPct(9, 12)).toBe(75)
    expect(matchedPct(0, 12)).toBe(0)
    expect(matchedPct(12, 12)).toBe(100)
  })
  it('total 為 0 不除以零', () => {
    expect(matchedPct(0, 0)).toBe(0)
  })
})

describe('stance 顯示與色調', () => {
  it('依維度用各自的受控詞彙（風險用趨緩/持平/升高）', () => {
    expect(stanceDisplay('outlook', 'positive')).toBe('正面')
    expect(stanceDisplay('valuation', 'attractive')).toBe('偏低')
    expect(stanceDisplay('risk', 'rising')).toBe('升高')
    expect(stanceDisplay('risk', 'easing')).toBe('趨緩')
  })
  it('風險升高＝建設性下降（色調為負，不因字面 positive/negative 而反轉）', () => {
    expect(stanceTone('risk', 'rising')).toBe('neg')
    expect(stanceTone('risk', 'easing')).toBe('pos')
    expect(stanceTone('valuation', 'stretched')).toBe('neg')
  })
  it('未映射的 stance 原樣顯示、色調退回中性', () => {
    expect(stanceDisplay('outlook', 'weird')).toBe('weird')
    expect(stanceTone('outlook', 'weird')).toBe('neu')
  })
  it('stance 為 null 時不給標籤', () => {
    expect(stanceDisplay('outlook', null)).toBeNull()
    expect(stanceTone('outlook', null)).toBe('neu')
  })
})

describe('targetSub / fmtTargetPrice', () => {
  it('幣別或期間缺一時不留分隔', () => {
    expect(targetSub('TWD', '12 個月')).toBe('TWD · 12 個月')
    expect(targetSub('TWD', null)).toBe('TWD')
    expect(targetSub(null, '12 個月')).toBe('12 個月')
    expect(targetSub(null, null)).toBe('')
  })
  it('缺值回破折號', () => {
    expect(fmtTargetPrice(null)).toBe('—')
    expect(fmtTargetPrice(undefined)).toBe('—')
  })
  it('整數不補小數', () => {
    expect(fmtTargetPrice(2444)).toBe('2,444')
  })
})

describe('isJumpable', () => {
  it('quote_start 為 null → 不可跳', () => {
    expect(isJumpable(tk({ quote_start: null, quote_end: 10 }))).toBe(false)
  })
  it('end 未大於 start → 不可跳', () => {
    expect(isJumpable(tk({ quote_start: 10, quote_end: 10 }))).toBe(false)
  })
  it('有合法區間 → 可跳', () => {
    expect(isJumpable(tk({ quote_start: 0, quote_end: 4 }))).toBe(true)
  })
})

describe('buildTextSegments', () => {
  const text = '0123456789'

  it('無摘錄時整篇為單一段', () => {
    expect(buildTextSegments(text, [])).toEqual([{ key: 't0', text: '0123456789' }])
  })

  it('依 offset 切出引文段並保留前後文', () => {
    const segs = buildTextSegments(text, [tk({ ordinal: 1, quote_start: 2, quote_end: 5 })])
    expect(segs).toEqual([
      { key: 't0', text: '01' },
      { key: 'q1', text: '234', ordinal: 1 },
      { key: 't5', text: '56789' },
    ])
  })

  it('引文自 0 起始時前面不插入空白段', () => {
    const segs = buildTextSegments(text, [tk({ ordinal: 1, quote_start: 0, quote_end: 3 })])
    expect(segs).toEqual([
      { key: 'q1', text: '012', ordinal: 1 },
      { key: 't3', text: '3456789' },
    ])
  })

  it('多條摘錄依起點排序切片（輸入順序不影響結果）', () => {
    const segs = buildTextSegments(text, [
      tk({ ordinal: 2, quote_start: 6, quote_end: 8 }),
      tk({ ordinal: 1, quote_start: 1, quote_end: 3 }),
    ])
    expect(segs.filter(s => s.ordinal != null)).toEqual([
      { key: 'q1', text: '12', ordinal: 1 },
      { key: 'q2', text: '67', ordinal: 2 },
    ])
  })

  it('重疊者略過（保留先到者，避免切片錯位）', () => {
    const segs = buildTextSegments(text, [
      tk({ ordinal: 1, quote_start: 0, quote_end: 5 }),
      tk({ ordinal: 2, quote_start: 3, quote_end: 7 }),
    ])
    expect(segs.map(s => s.ordinal).filter(Boolean)).toEqual([1])
  })

  it('越界的 offset 略過（不丟例外、不切出錯字）', () => {
    const segs = buildTextSegments(text, [tk({ ordinal: 1, quote_start: 5, quote_end: 999 })])
    expect(segs).toEqual([{ key: 't0', text: '0123456789' }])
  })

  it('錨不到（quote_start 為 null）的摘錄不參與切片', () => {
    const segs = buildTextSegments(text, [tk({ ordinal: 1, quote_start: null, quote_end: null })])
    expect(segs).toEqual([{ key: 't0', text: '0123456789' }])
  })

  it('切片後拼回等於原文（不遺失也不重複任何字元）', () => {
    const segs = buildTextSegments(text, [
      tk({ ordinal: 1, quote_start: 1, quote_end: 3 }),
      tk({ ordinal: 2, quote_start: 6, quote_end: 10 }),
    ])
    expect(segs.map(s => s.text).join('')).toBe(text)
  })
})

// 後端 offset 以 Python str（code point）計；JS 字串是 UTF-16。星平面字元（罕用 CJK
// 擴充區、emoji）一個 code point 佔兩個 UTF-16 單位——若用 text.slice/text.length 套
// offset，之後全部邊界右移、高亮/跳段落到錯字（sha 驗不出來）。這組把 code point 座標釘死。
describe('buildTextSegments（星平面字元 / code point offset）', () => {
  // '𠀀' 為 CJK 擴充 B（1 code point = 2 UTF-16 單位）；code point 序列為
  // [𠀀, 一, 二, 三, 四]，長度 5。若誤用 UTF-16 索引，[2,4) 會切到「一二」而非「二三」。
  const text = '𠀀一二三四'

  it('引文 offset 以 code point 計，星平面字元後不右移', () => {
    const segs = buildTextSegments(text, [tk({ ordinal: 1, quote_start: 2, quote_end: 4 })])
    expect(segs.find(s => s.ordinal === 1)?.text).toBe('二三')
  })

  it('命中段 offset 同樣以 code point 計', () => {
    const segs = buildTextSegments(text, [], { start: 1, end: 3 })
    expect(segs.find(s => s.hit)?.text).toBe('一二')
  })

  it('越界判斷用 code point 長度（末端引文不被 UTF-16 長度誤判為越界）', () => {
    // code point 長度為 5：[3,5)=「三四」合法；若誤用 text.length（UTF-16=6）雖也放行，
    // 但切片會取到 UTF-16 的 [3,5) 而非「三四」。這條同時釘住長度與切片兩處。
    const segs = buildTextSegments(text, [tk({ ordinal: 1, quote_start: 3, quote_end: 5 })])
    expect(segs.find(s => s.ordinal === 1)?.text).toBe('三四')
  })

  it('混合星平面字元切片後拼回仍等於原文（不遺失也不切壞代理對）', () => {
    const mixed = '𠀀一😀三𪜀五'
    const segs = buildTextSegments(mixed, [tk({ ordinal: 1, quote_start: 2, quote_end: 4 })])
    expect(segs.map(s => s.text).join('')).toBe(mixed)
    // [2,4) → 「😀三」（😀 也是星平面）
    expect(segs.find(s => s.ordinal === 1)?.text).toBe('😀三')
  })
})

// 命中段＝?chunk= 帶進來的那一段，offset 同樣由後端 anchor.py 算，前端只切片
describe('buildTextSegments（命中段）', () => {
  const text = '0123456789'

  it('無命中時整篇都不標', () => {
    expect(buildTextSegments(text, [], null).some(s => s.hit)).toBe(false)
    expect(buildTextSegments(text, []).some(s => s.hit)).toBe(false)
  })

  it('一般內文在命中邊界切開，只有區間內的段標 hit', () => {
    expect(buildTextSegments(text, [], { start: 3, end: 6 })).toEqual([
      { key: 't0', text: '012' },
      { key: 't3', text: '345', hit: true },
      { key: 't6', text: '6789' },
    ])
  })

  it('命中自 0 起始／延伸到文末時不切出空白段', () => {
    expect(buildTextSegments(text, [], { start: 0, end: 10 })).toEqual([
      { key: 't0', text: '0123456789', hit: true },
    ])
  })

  it('越界的命中夾回文字長度，反向／空區間視為無命中', () => {
    expect(buildTextSegments(text, [], { start: 8, end: 999 })).toEqual([
      { key: 't0', text: '01234567' },
      { key: 't8', text: '89', hit: true },
    ])
    expect(buildTextSegments(text, [], { start: 5, end: 5 }).some(s => s.hit)).toBe(false)
    expect(buildTextSegments(text, [], { start: 7, end: 3 }).some(s => s.hit)).toBe(false)
  })

  it('引文段落在命中內時整段標 hit（不切，以保住 data-q 的唯一性）', () => {
    const segs = buildTextSegments(text, [tk({ ordinal: 1, quote_start: 2, quote_end: 5 })], { start: 4, end: 8 })
    expect(segs).toEqual([
      { key: 't0', text: '01' },
      { key: 'q1', text: '234', ordinal: 1, hit: true },
      { key: 't5', text: '567', hit: true },
      { key: 't8', text: '89' },
    ])
  })

  it('引文在命中之外時不標 hit', () => {
    const segs = buildTextSegments(text, [tk({ ordinal: 1, quote_start: 0, quote_end: 2 })], { start: 5, end: 8 })
    expect(segs.find(s => s.ordinal === 1)?.hit).toBeUndefined()
  })

  it('切片後拼回仍等於原文（命中切開也不遺失字元）', () => {
    const segs = buildTextSegments(text, [tk({ ordinal: 1, quote_start: 1, quote_end: 3 })], { start: 2, end: 7 })
    expect(segs.map(s => s.text).join('')).toBe(text)
  })
})
