import type { BriefMarketCount, BriefReportRef } from '../../lib/briefSchemas'
import { MARKET_ORDER, marketColor, marketLabel } from '../../lib/meta'

/**
 * 把簡報的 markdown 拆成畫面上的區塊（今日重點、市場焦點），以及概況卡的市場分布。
 *
 * 簡報本文由 `scripts/generate_brief.py` 的 prompt 固定成三段（今日重點、市場焦點、評等與目標價），
 * 但格式細節是模型寫的：市場焦點的市場名可能是 `### 台股`、`**台股**` 獨立一行或 `**台股**：內文`，
 * 名稱也不一定是後端 `market_zh()` 給的那一組。這裡只做容錯的拆段，認不出來的東西**不丟**：
 * 拆不出任何已知段落時整份交給 markdown 渲染器（見 BriefPage），多出來的段落另列。
 *
 * 「評等與目標價」那一段刻意不顯示（介面改版，使用者要求拿掉評等與目標價區塊）。
 */

export interface BriefHighlight {
  /** 去掉句末（券商）之後的內文。 */
  text: string
  /** 句末標示的來源券商（已換成中文名）；沒標就是空陣列。 */
  brokers: string[]
}

export interface BriefFocusBlock {
  /** 顯示用的市場名：認得的市場一律用介面的名稱（與概況卡圖例一致），其餘照原文。 */
  label: string | null
  /** findb 市場代碼；認不出來是 null（不畫市場色、不顯示篇數）。 */
  market: string | null
  paragraphs: string[]
}

export interface BriefExtraSection {
  title: string
  body: string
}

export interface BriefContent {
  highlights: BriefHighlight[]
  focus: BriefFocusBlock[]
  /** prompt 以外的段落（模型偶爾會多寫）：照原樣以 markdown 呈現，不靜默丟掉。 */
  extras: BriefExtraSection[]
  /** 今日重點與市場焦點都拆不出來時，整份（扣掉評等段）照 markdown 呈現。 */
  fallback: string | null
}

/** 概況卡的市場分布（一個市場一格；未標市場與認不出的代碼併成「其他」殿後）。 */
export interface BriefMixItem {
  market: string | null
  label: string
  color: string
  count: number
}

// 「其他」與分布條的中性灰：設計稿的值。
export const OTHER_COLOR = '#c7c7cc'

const KNOWN_MARKETS = new Set<string>(MARKET_ORDER)

// 模型寫出來的市場名 → findb 代碼。前兩組是後端 market_zh() 與介面 meta.ts 的名稱，
// 其餘是實際簡報裡出現過或同義的寫法。
const MARKET_ALIASES: Record<string, string> = {
  台股: 'TW', 臺股: 'TW', 台灣: 'TW',
  美股: 'US', 美國: 'US',
  港股: 'HK', 香港: 'HK',
  陸股: 'CN', A股: 'CN', 中國: 'CN',
  外匯: 'FX', 匯市: 'FX', 匯率: 'FX',
  台指期: 'WTX', 期貨: 'WTX',
  總經: 'MACRO', 總體: 'MACRO', 總體經濟: 'MACRO', 總體與債市: 'MACRO', 總經與債市: 'MACRO', 債市: 'MACRO',
  全球: 'GLOBAL', 全球與商品: 'GLOBAL', 商品: 'GLOBAL', 國際: 'GLOBAL',
  加密: 'CRYPTO', 加密資產: 'CRYPTO', 加密貨幣: 'CRYPTO',
}

export function marketOfLabel(raw: string): string | null {
  const name = raw.replace(/[*#：:]/g, '').trim()
  if (!name) return null
  const upper = name.toUpperCase()
  if (KNOWN_MARKETS.has(upper)) return upper
  return MARKET_ALIASES[name] ?? MARKET_ALIASES[name.replace(/市場$/, '')] ?? null
}

/** 來源代碼 → 中文名（取自本期來源研報）。鍵一律小寫。 */
export function sourceLabels(reports: readonly BriefReportRef[]): Map<string, string> {
  const map = new Map<string, string>()
  for (const r of reports) {
    if (r.source && r.source_display) map.set(r.source.toLowerCase(), r.source_display)
  }
  return map
}

const BROKER_SPLIT = /[、,，/／]/

function localizeBroker(name: string, labels: Map<string, string>): string {
  const t = name.trim()
  return labels.get(t.toLowerCase()) ?? t
}

/**
 * 素材裡的來源是原始代碼（yuanta、kgi），模型常照抄成「（kgi）」。括號裡**每一個**名字都是
 * 本期來源的代碼時才換成中文名；其餘括號（數字、說明）原樣保留。
 */
export function localizeSources(text: string, labels: Map<string, string>): string {
  if (labels.size === 0) return text
  return text.replace(/([（(])([^（）()]{1,60})([）)])/g, (whole, open: string, inner: string, close: string) => {
    const parts = inner.split(BROKER_SPLIT).map(s => s.trim()).filter(Boolean)
    if (parts.length === 0 || !parts.every(p => labels.has(p.toLowerCase()))) return whole
    return `${open}${parts.map(p => labels.get(p.toLowerCase())).join('、')}${close}`
  })
}

// 句末的（券商）：prompt 規定今日重點每條都這樣標來源。含數字的括號是內文（幅度、日期），不是來源。
const TRAILING_SOURCE = /\s*[（(]([^（）()\d]{1,40})[）)]\s*([。．.]?)\s*$/

export function splitHighlight(item: string, labels: Map<string, string>): BriefHighlight {
  const m = TRAILING_SOURCE.exec(item)
  if (!m) return { text: localizeSources(item.trim(), labels), brokers: [] }
  const brokers = m[1].split(BROKER_SPLIT).map(s => s.trim()).filter(Boolean).map(b => localizeBroker(b, labels))
  const body = item.slice(0, m.index).trimEnd()
  const text = /[。！？.!?]$/.test(body) ? body : body + m[2]
  return { text: localizeSources(text, labels), brokers: [...new Set(brokers)] }
}

const LIST_ITEM = /^\s*(?:[-*+•]|\d{1,2}[.)、])\s+(.*)$/
const EMPTY_SECTION = /^(本期無|無)[。.]?$/

function isEmptyBody(lines: string[]): boolean {
  const text = lines.map(l => l.trim()).filter(Boolean)
  return text.length === 0 || (text.length === 1 && EMPTY_SECTION.test(text[0].replace(/^[-*+•]\s*/, '')))
}

function parseHighlights(lines: string[], labels: Map<string, string>): BriefHighlight[] {
  if (isEmptyBody(lines)) return []
  const items: string[] = []
  let sawList = false
  for (const raw of lines) {
    const line = raw.trim()
    if (!line) continue
    const m = LIST_ITEM.exec(raw)
    if (m) {
      sawList = true
      items.push(m[1].trim())
    } else if (sawList && items.length > 0) {
      // 清單項目的續行（模型偶爾斷行）：中文直接接上。
      items[items.length - 1] += line
    } else {
      items.push(line)
    }
  }
  return items.filter(Boolean).map(t => splitHighlight(t, labels))
}

// `**台股**`、`**台股**：內文`、`**台股：**內文`
const BOLD_LABEL = /^\*\*([^*]{1,20}?)[：:]?\*\*\s*[：:]?\s*(.*)$/
const HEADING_LABEL = /^#{3,6}\s+(.+?)\s*$/

function parseFocus(lines: string[], labels: Map<string, string>): BriefFocusBlock[] {
  if (isEmptyBody(lines)) return []
  const blocks: { label: string | null; paragraphs: string[]; open: boolean }[] = []
  const current = () => blocks[blocks.length - 1]
  const start = (label: string | null) => blocks.push({ label, paragraphs: [], open: false })
  const addText = (text: string) => {
    if (!text) return
    if (blocks.length === 0) start(null)
    const block = current()
    if (block.open) block.paragraphs[block.paragraphs.length - 1] += text
    else block.paragraphs.push(text)
    block.open = true
  }

  for (const raw of lines) {
    const listed = LIST_ITEM.exec(raw)
    const line = (listed ? listed[1] : raw).trim()
    if (!line) {
      if (blocks.length) current().open = false
      continue
    }
    const heading = HEADING_LABEL.exec(line)
    const boldMatch = heading ? null : BOLD_LABEL.exec(line)
    // 段首的粗體不一定是市場名（「**台積電**宣布漲價…」）：獨立一行、後面接冒號、或認得出是市場才算。
    const bold = boldMatch && (!boldMatch[2] || /[：:]\s*\*\*|\*\*\s*[：:]/.test(line) || marketOfLabel(boldMatch[1]))
      ? boldMatch
      : null
    if (heading) {
      start(heading[1].replace(/[*：:]/g, '').trim())
    } else if (bold) {
      start(bold[1].trim())
      addText(bold[2].trim())
      // 同一行的內文自成一段：下一行的獨立文字是新段落。
      current().open = false
    } else {
      addText(line)
    }
  }

  return blocks
    .filter(b => b.paragraphs.length > 0 || b.label)
    .map(b => {
      const market = b.label ? marketOfLabel(b.label) : null
      return {
        label: market ? marketLabel(market) : b.label,
        market,
        paragraphs: b.paragraphs.map(p => localizeSources(p, labels)),
      }
    })
    .filter(b => b.paragraphs.length > 0)
}

interface Section {
  title: string
  lines: string[]
}

function splitSections(markdown: string): { preamble: string[]; sections: Section[] } {
  const preamble: string[] = []
  const sections: Section[] = []
  for (const line of markdown.split('\n')) {
    const h = /^#{1,2}\s+(.+?)\s*#*\s*$/.exec(line)
    if (h) sections.push({ title: h[1].replace(/\*\*/g, '').trim(), lines: [] })
    else if (sections.length) sections[sections.length - 1].lines.push(line)
    else preamble.push(line)
  }
  return { preamble, sections }
}

const isRatings = (title: string) => title.includes('評等') || title.includes('目標價')

export function parseBriefMarkdown(markdown: string, reports: readonly BriefReportRef[] = []): BriefContent {
  const labels = sourceLabels(reports)
  const { preamble, sections } = splitSections(markdown)
  let highlights: BriefHighlight[] | null = null
  let focus: BriefFocusBlock[] | null = null
  const extras: BriefExtraSection[] = []

  for (const s of sections) {
    if (highlights === null && s.title.includes('今日重點')) highlights = parseHighlights(s.lines, labels)
    else if (focus === null && s.title.includes('市場焦點')) focus = parseFocus(s.lines, labels)
    else if (isRatings(s.title)) continue
    else if (!isEmptyBody(s.lines)) extras.push({ title: s.title, body: s.lines.join('\n').trim() })
  }

  if (highlights === null && focus === null) {
    // 一個已知段落都認不出來：格式漂掉了。整份照 markdown 呈現（評等段照樣拿掉），不吞內容。
    const kept = sections
      .filter(s => !isRatings(s.title))
      .map(s => `## ${s.title}\n${s.lines.join('\n')}`)
    const body = [preamble.join('\n').trim(), ...kept].filter(Boolean).join('\n\n').trim()
    return { highlights: [], focus: [], extras: [], fallback: body || null }
  }
  return { highlights: highlights ?? [], focus: focus ?? [], extras, fallback: null }
}

/** 概況卡的市場分布：已知市場依篇數由多到少（同篇數依 MARKET_ORDER），「其他」殿後。 */
export function briefMix(markets: readonly BriefMarketCount[] | null | undefined): BriefMixItem[] {
  if (!markets) return []
  const known = new Map<string, number>()
  let other = 0
  for (const m of markets) {
    const code = m.market?.toUpperCase() ?? null
    if (code && KNOWN_MARKETS.has(code)) known.set(code, (known.get(code) ?? 0) + m.report_count)
    else other += m.report_count
  }
  const order = (code: string) => (MARKET_ORDER as readonly string[]).indexOf(code)
  const items: BriefMixItem[] = [...known.entries()]
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1] || order(a[0]) - order(b[0]))
    .map(([code, n]) => ({ market: code, label: marketLabel(code), color: marketColor(code), count: n }))
  if (other > 0) items.push({ market: null, label: '其他', color: OTHER_COLOR, count: other })
  return items
}
