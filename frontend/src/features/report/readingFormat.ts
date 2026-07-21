import type { ReadingDoc, ReadingRatingNorm, Takeaway, ThesisKey } from '../../lib/readingSchemas'

/** file_hash 為 sha256 十六進位；非 64-hex 前端先擋一次（不打 API）。 */
const HASH_RE = /^[0-9a-f]{64}$/i
export function isValidHash(hash: string | null | undefined): boolean {
  return typeof hash === 'string' && HASH_RE.test(hash)
}

/** 閱讀頁連結；帶 chunk 時閱讀頁預設落在文字檢視。 */
export function reportHref(fileHash: string, chunkIndex?: number | null): string {
  const base = `/report/${fileHash}`
  return chunkIndex == null ? base : `${base}?chunk=${chunkIndex}`
}

export const RATING_DISPLAY: Record<ReadingRatingNorm, string> = {
  buy: '買進',
  overweight: '加碼',
  neutral: '中立',
  underweight: '減碼',
  sell: '賣出',
  unknown: '未評等',
}

/** 評等 → 三桶語意（著色用）。 */
export const RATING_BUCKET: Record<ReadingRatingNorm, 'bull' | 'neu' | 'bear'> = {
  buy: 'bull',
  overweight: 'bull',
  neutral: 'neu',
  underweight: 'bear',
  sell: 'bear',
  unknown: 'neu',
}

export const THESIS_LABEL: Record<ThesisKey, string> = {
  outlook: '展望',
  catalyst: '催化',
  risk: '風險',
  valuation: '估值',
}

/** 四維論點的顯示順序（對齊後端 signal_extract.THESIS_DIMENSIONS）。 */
export const THESIS_ORDER: ThesisKey[] = ['outlook', 'catalyst', 'risk', 'valuation']

/**
 * stance 受控詞彙 → 中文顯示，逐字鏡像後端 signal_extract.STANCE_CONSTRUCTIVENESS 的維度特定詞彙。
 *
 * 注意：這是「單篇報告的靜態立場」，不是雷達的「跨報告變化」。故用「正面/中性/負面」
 * 而非「轉強/轉弱」——單篇沒有前次可比，說「轉強」會是無中生有的比較敘述。
 * 風險維度以「升高＝建設性下降」表達，避免 positive/negative 對風險的符號歧義。
 */
const STANCE_DISPLAY: Record<ThesisKey, Record<string, string>> = {
  outlook: { positive: '正面', neutral: '中性', negative: '負面' },
  catalyst: { positive: '正面', neutral: '中性', negative: '負面' },
  valuation: { attractive: '偏低', fair: '合理', stretched: '偏高' },
  risk: { easing: '趨緩', stable: '持平', rising: '升高' },
}

/** 建設性序位（+1/0/−1）→ 色調桶，鏡像後端同名對照表。 */
const STANCE_TONE: Record<ThesisKey, Record<string, 'pos' | 'neu' | 'neg'>> = {
  outlook: { positive: 'pos', neutral: 'neu', negative: 'neg' },
  catalyst: { positive: 'pos', neutral: 'neu', negative: 'neg' },
  valuation: { attractive: 'pos', fair: 'neu', stretched: 'neg' },
  risk: { easing: 'pos', stable: 'neu', rising: 'neg' },
}

/** 未映射的 stance 原樣顯示（不猜、不丟），色調退回中性。 */
export function stanceDisplay(key: ThesisKey, stance: string | null | undefined): string | null {
  if (!stance) return null
  return STANCE_DISPLAY[key]?.[stance] ?? stance
}

export function stanceTone(key: ThesisKey, stance: string | null | undefined): 'pos' | 'neu' | 'neg' {
  if (!stance) return 'neu'
  return STANCE_TONE[key]?.[stance] ?? 'neu'
}

/**
 * 報頭 meta 組字串：券商 · 日期。任一為 null 時不留多餘的分隔點。
 * source_display 優先於 source（顯示名 vs 內部代號）。
 */
export function metaParts(doc: Pick<ReadingDoc, 'source' | 'source_display' | 'report_date'>): string[] {
  const broker = doc.source_display || doc.source || null
  const date = doc.report_date ? doc.report_date.slice(0, 10) : null
  return [broker, date].filter((s): s is string => Boolean(s))
}

/** 相似研報卡的 meta：券商 · 日期（同樣不留空分隔）。 */
export function similarMeta(item: {
  source?: string | null
  source_display?: string | null
  report_date?: string | null
}): string {
  const broker = item.source_display || item.source || null
  const date = item.report_date ? item.report_date.slice(0, 10) : null
  return [broker, date].filter(Boolean).join(' · ')
}

/** 設計稿拍板的相似度說法：「9/12 段相符」。 */
export function matchedText(matched: number, total: number): string {
  return `${matched}/${total} 段相符`
}

/** 相似度條寬度百分比；total 為 0 時回 0（不除以零）。 */
export function matchedPct(matched: number, total: number): number {
  if (!total) return 0
  return Math.max(0, Math.min(100, Math.round((matched / total) * 100)))
}

const CURRENCY_PREFIX: Record<string, string> = {
  TWD: 'NT$',
  USD: 'US$',
  HKD: 'HK$',
  CNY: 'CN¥',
  JPY: '¥',
}

/** 目標價數值（不含幣別；幣別走 fig 副標）。 */
export function fmtTargetPrice(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  return value.toLocaleString('zh-TW', {
    minimumFractionDigits: Number.isInteger(value) ? 0 : 2,
    maximumFractionDigits: 2,
  })
}

/** 目標價副標：幣別 · 期間（缺值不留空分隔）。 */
export function targetSub(
  currency: string | null | undefined,
  horizon: string | null | undefined,
): string {
  return [currency, horizon].filter(Boolean).join(' · ')
}

export function currencyPrefix(currency: string | null | undefined): string {
  if (!currency) return ''
  return CURRENCY_PREFIX[currency] ?? `${currency} `
}

/** 摘錄可否跳轉：需有 quote_start/quote_end，且正典文字未漂移（sha 相符）。 */
export function isJumpable(t: Takeaway): boolean {
  return t.quote_start != null && t.quote_end != null && t.quote_end > t.quote_start
}

/** 文字檢視的引文 DOM 標記；以 ordinal 為鍵（後端保證同篇內唯一）。 */
export function quoteAttr(ordinal: number): string {
  return `q${ordinal}`
}

export interface TextSegment {
  key: string
  text: string
  /** 有值＝此段為某條摘錄的引文，需上標記可跳轉；無值＝一般內文。 */
  ordinal?: number
  /** 此段落在檢索命中的區間內（?chunk= 帶進來的那一段）。 */
  hit?: true
}

/** 檢索命中段的字元區間（後端 /text?chunk= 回的 chunk_start/chunk_end）。 */
export interface HitRange {
  start: number
  end: number
}

/** 命中區間與正典文字的交集；越界、反向、錨不到一律視為無命中。len 為 code point 數。 */
function clampHit(len: number, hit: HitRange | null | undefined): HitRange | null {
  if (!hit) return null
  const start = Math.max(0, hit.start)
  const end = Math.min(len, hit.end)
  return end > start ? { start, end } : null
}

/** 區間 [start, end) 是否與命中段有交集。 */
function inHit(hit: HitRange | null, start: number, end: number): true | undefined {
  return hit && start < hit.end && end > hit.start ? true : undefined
}

/**
 * 依 takeaways 的 quote_start/quote_end 把正典文字切成 segment，並標出命中段。
 *
 * offset 一律由後端（app/services/reading/anchor.py）算好，前端只做切片 ——
 * 不在此重造任何正規化比對邏輯。越界、反向、彼此重疊者略過（先到先得），
 * 略過的摘錄仍會在左欄顯示，只是不可跳。
 *
 * **座標系**：後端 offset 以 Python str（Unicode code point）為單位，但 JS 字串是
 * UTF-16 —— 星平面字元（罕用 CJK 擴充區、emoji）一個 code point 佔兩個 UTF-16 單位。
 * 若直接用 text.slice/text.length 套 offset，遇到這類字元後所有邊界會右移，高亮與跳段
 * 靜靜落到錯字上（text_sha256 雜湊 UTF-8 位元組、驗不出這種漂移）。故先把文字拆成
 * code point 陣列，全程以 code point 索引，與後端座標系一致。
 *
 * 命中段（hit）與引文是兩套獨立的 offset：一般內文會在命中邊界切開，讓區間內外分別
 * 上色；引文段則整段一起標（不切）—— 切開會讓同一 ordinal 出現兩個 data-q，跳轉錨點
 * 就失去唯一性，而引文最多只會跨越命中邊界一次，視覺誤差可忽略。
 */
export function buildTextSegments(
  text: string,
  takeaways: Takeaway[],
  hitRange?: HitRange | null,
): TextSegment[] {
  // code point 陣列：cp[i] 對應後端 offset i（見上方座標系說明）。
  const cp = Array.from(text)
  const len = cp.length
  const slice = (a: number, b: number) => cp.slice(a, b).join('')

  const hit = clampHit(len, hitRange)
  const ranges = takeaways
    .filter(isJumpable)
    .map(t => ({ start: t.quote_start as number, end: t.quote_end as number, ordinal: t.ordinal }))
    .filter(r => r.start >= 0 && r.end <= len && r.end > r.start)
    .sort((a, b) => a.start - b.start)

  const segments: TextSegment[] = []
  let cursor = 0

  // 一般內文：在命中邊界切開，命中段才標得出來（key 用絕對起點，切幾段都唯一）
  const pushPlain = (from: number, to: number) => {
    if (to <= from) return
    const cuts = hit ? [hit.start, hit.end].filter(c => c > from && c < to) : []
    const bounds = [from, ...cuts, to]
    for (let i = 0; i < bounds.length - 1; i++) {
      const [a, b] = [bounds[i], bounds[i + 1]]
      segments.push({ key: `t${a}`, text: slice(a, b), hit: inHit(hit, a, b) })
    }
  }

  for (const r of ranges) {
    // 與前一段重疊 → 略過（保留先到者，避免切片錯位）
    if (r.start < cursor) continue
    pushPlain(cursor, r.start)
    segments.push({
      key: `q${r.ordinal}`,
      text: slice(r.start, r.end),
      ordinal: r.ordinal,
      hit: inHit(hit, r.start, r.end),
    })
    cursor = r.end
  }
  pushPlain(cursor, len)
  return segments
}
