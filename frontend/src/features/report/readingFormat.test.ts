import { describe, it, expect } from 'vitest'
import {
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
  // 反向釘死：閱讀頁已無命中定位的落點，連結不得再帶任何 query。
  // 帶了不會報錯、只會在網址列留下一個沒有消費端的參數。
  it('永遠是乾淨的閱讀頁網址，不帶 query', () => {
    expect(reportHref(HASH)).toBe(`/report/${HASH}`)
    expect(reportHref(HASH)).not.toContain('?')
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
