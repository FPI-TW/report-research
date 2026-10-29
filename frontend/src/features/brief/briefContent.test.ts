import { describe, expect, it } from 'vitest'
import type { BriefReportRef } from '../../lib/briefSchemas'
import { OTHER_COLOR, briefMix, localizeSources, marketOfLabel, parseBriefMarkdown, splitHighlight } from './briefContent'

function ref(source: string, display: string): BriefReportRef {
  return { report_id: source, file_hash: source, file_name: `${source}.pdf`, source, source_display: display }
}
const REPORTS = [ref('yuanta', '元大'), ref('kgi', '凱基')]
const LABELS = new Map([['yuanta', '元大'], ['kgi', '凱基']])

// 以下三種市場焦點寫法都是 devdb 裡實際出現過的簡報（節錄）。
const BOLD_LINE = [
  '## 今日重點',
  '- 美國 9 月非農就業僅新增 2.9 萬人、遠低於預期，美股主要指數收高（yuanta）。',
  '- 台股上週沿 5 日均線碎步向上（yuanta）。',
  '',
  '## 市場焦點',
  '**美股**',
  '美國 9 月非農就業新增 2.9 萬人。（yuanta）',
  '',
  '**台股**',
  '台股上週沿 5 日均線碎步向上（yuanta）。',
  '',
  '**總經**',
  '美國 9 月非農就業放緩。',
  '',
  '## 評等與目標價',
  '南電（TW 8046）｜yuanta｜評等 未提供｜目標價 TWD 340→TWD 1700（+400.0%）',
].join('\n')

const HEADING = [
  '## 今日重點',
  '- 台股創歷史新高（kgi）。',
  '',
  '## 市場焦點',
  '### 總體與債市',
  '聯準會官員釋出暫緩升息訊號（yuanta）。',
  '',
  '### 台指期',
  '台指期延續多頭格局（kgi）。9 月 30 日大漲 549 點（kgi）。',
  '',
  '## 評等與目標價',
  '- 金像電（TW 2368）｜kgi｜評等 未提供',
].join('\n')

const INLINE_LABEL = [
  '## 今日重點',
  '- 中國信託證券調升聚陽評等至加碼（中國信託證券）。',
  '',
  '## 市場焦點',
  '**台股**：08/11 開低走高，收復 4 萬 5 關卡（凱基證券）。',
  '',
  '**全球與商品**：兩家券商每日均有更新（元大投顧、中國信託證券）。',
].join('\n')

describe('parseBriefMarkdown', () => {
  it('今日重點：去掉句末來源、換成中文券商名，句號留著', () => {
    const c = parseBriefMarkdown(BOLD_LINE, REPORTS)
    expect(c.highlights).toEqual([
      { text: '美國 9 月非農就業僅新增 2.9 萬人、遠低於預期，美股主要指數收高。', brokers: ['元大'] },
      { text: '台股上週沿 5 日均線碎步向上。', brokers: ['元大'] },
    ])
  })

  it('市場焦點：獨立一行的粗體市場名，市場名換成介面名稱、內文的來源代碼換成中文', () => {
    const c = parseBriefMarkdown(BOLD_LINE, REPORTS)
    expect(c.focus.map(b => [b.label, b.market])).toEqual([['美股', 'US'], ['台股', 'TW'], ['總經', 'MACRO']])
    expect(c.focus[1].paragraphs).toEqual(['台股上週沿 5 日均線碎步向上（元大）。'])
  })

  it('市場焦點：### 標題寫法；後端的市場名（總體與債市）對回介面名稱', () => {
    const c = parseBriefMarkdown(HEADING, REPORTS)
    expect(c.focus.map(b => [b.label, b.market])).toEqual([['總經', 'MACRO'], ['台指期', 'WTX']])
    expect(c.focus[1].paragraphs).toEqual(['台指期延續多頭格局（凱基）。9 月 30 日大漲 549 點（凱基）。'])
  })

  it('市場焦點：「**市場**：內文」同一行的寫法，多家來源以頓號分開', () => {
    const c = parseBriefMarkdown(INLINE_LABEL, [])
    expect(c.focus).toEqual([
      { label: '台股', market: 'TW', paragraphs: ['08/11 開低走高，收復 4 萬 5 關卡（凱基證券）。'] },
      { label: '全球', market: 'GLOBAL', paragraphs: ['兩家券商每日均有更新（元大投顧、中國信託證券）。'] },
    ])
    expect(c.highlights[0].brokers).toEqual(['中國信託證券'])
  })

  it('評等與目標價整段不顯示', () => {
    for (const md of [BOLD_LINE, HEADING]) {
      const c = parseBriefMarkdown(md, REPORTS)
      const all = JSON.stringify(c)
      expect(all).not.toContain('目標價')
      expect(all).not.toContain('南電')
      expect(all).not.toContain('金像電')
      expect(c.fallback).toBeNull()
    }
  })

  it('段首粗體不是市場名時不當成分段', () => {
    const c = parseBriefMarkdown('## 市場焦點\n**台積電**宣布調漲報價，帶動族群走強。', [])
    expect(c.focus).toEqual([{ label: null, market: null, paragraphs: ['**台積電**宣布調漲報價，帶動族群走強。'] }])
  })

  it('沒有分市場的市場焦點仍是一塊（不畫市場色與篇數）', () => {
    const c = parseBriefMarkdown('## 今日重點\n- 一句話\n\n## 市場焦點\n台股量縮整理，電子股相對強勢。', [])
    expect(c.focus).toEqual([{ label: null, market: null, paragraphs: ['台股量縮整理，電子股相對強勢。'] }])
  })

  it('「本期無」的段落是空的', () => {
    const c = parseBriefMarkdown('## 今日重點\n- 一句話（kgi）\n\n## 市場焦點\n本期無', REPORTS)
    expect(c.focus).toEqual([])
    expect(c.highlights).toHaveLength(1)
  })

  it('prompt 以外的段落另列，不靜默丟掉', () => {
    const c = parseBriefMarkdown('## 今日重點\n- 一句話\n\n## 補充說明\n當日資料較少。', [])
    expect(c.extras).toEqual([{ title: '補充說明', body: '當日資料較少。' }])
  })

  it('一個已知段落都認不出來時整份照 markdown 呈現（評等段照樣拿掉）', () => {
    const md = '## 重點整理\n- 一句話\n\n## 評等與目標價\n- 某標的｜某券商'
    const c = parseBriefMarkdown(md, [])
    expect(c.highlights).toEqual([])
    expect(c.fallback).toBe('## 重點整理\n- 一句話')
  })

  it('清單項目斷行的續行接回同一條', () => {
    const c = parseBriefMarkdown('## 今日重點\n- 第一句，\n  後半句（kgi）。\n- 第二條（kgi）', REPORTS)
    expect(c.highlights.map(h => h.text)).toEqual(['第一句，後半句。', '第二條'])
  })
})

describe('splitHighlight', () => {
  it('含數字的句末括號是內文，不是來源', () => {
    expect(splitHighlight('目標價上調（+32.1%）', LABELS)).toEqual({ text: '目標價上調（+32.1%）', brokers: [] })
  })

  it('沒標來源的條目原樣保留', () => {
    expect(splitHighlight('台股量縮整理。', LABELS)).toEqual({ text: '台股量縮整理。', brokers: [] })
  })
})

describe('localizeSources', () => {
  it('只換括號裡每個名字都是本期來源代碼的', () => {
    expect(localizeSources('甲（kgi）乙（kgi、yuanta）丙（摘要提及）丁（kgi、某券商）', LABELS))
      .toBe('甲（凱基）乙（凱基、元大）丙（摘要提及）丁（kgi、某券商）')
  })
})

describe('marketOfLabel', () => {
  it('介面名稱、後端名稱與代碼都認得', () => {
    expect(marketOfLabel('台股')).toBe('TW')
    expect(marketOfLabel('總體與債市')).toBe('MACRO')
    expect(marketOfLabel('匯市')).toBe('FX')
    expect(marketOfLabel('us')).toBe('US')
    expect(marketOfLabel('美股市場')).toBe('US')
    expect(marketOfLabel('能源')).toBeNull()
  })
})

describe('briefMix', () => {
  it('依篇數由多到少，未標市場與認不出的代碼併成「其他」殿後', () => {
    const mix = briefMix([
      { market: 'US', report_count: 3 },
      { market: null, report_count: 2 },
      { market: 'TW', report_count: 5 },
      { market: 'XX', report_count: 1 },
      { market: 'HK', report_count: 3 },
    ])
    expect(mix.map(m => [m.label, m.count])).toEqual([['台股', 5], ['美股', 3], ['港股', 3], ['其他', 3]])
    expect(mix[3]).toMatchObject({ market: null, color: OTHER_COLOR })
  })

  it('後端沒給分布（舊版或查詢降級）時是空的', () => {
    expect(briefMix(undefined)).toEqual([])
    expect(briefMix(null)).toEqual([])
  })
})
