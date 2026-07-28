import type { ReportStage } from './askSchemas'

/**
 * 深度研報的進度模型。全部是純函式——時間由呼叫端餵進來（elapsedMs），元件只負責每秒
 * 重算一次。
 *
 * 為什麼不是一個 stage → pct 的查表：一份逐節研報的 5–12 分鐘裡，有 7 成以上花在
 * `writing` 這一個 stage。查表版把它固定成 50% 配一根不定量掃光條，於是使用者盯著
 * 一個數字不動的畫面好幾分鐘——「看起來很像沒有在動」正是這麼來的。真正的分子分母
 * 在章節上（後端的 outline / section_draft / section_skipped 事件）。
 */

/** 各階段佔整條進度的區間。writing 獨佔最大一段，內部再依章節細分。 */
const BANDS: Record<ReportStage, [number, number]> = {
  retrieving: [0, 12],
  outlining: [12, 18],
  writing: [18, 88],
  // 網搜是 writing 途中的插曲，不是獨立階段：共用同一區間，只換文案。
  searching_web: [18, 88],
  verifying: [88, 94],
  rendering: [94, 99],
}

const STAGE_TEXT: Record<ReportStage, string> = {
  retrieving: '深度檢索研報中',
  outlining: '規劃章節結構中',
  writing: '撰寫研報中',
  searching_web: '搜尋網路補充中',
  verifying: '查核引用與數據中',
  rendering: '排版 PDF 中',
}

/** 生成尚未開始（連上背景任務之前）時的預設文案。 */
export const REPORT_PENDING_TEXT = '準備生成研報'

export interface ReportProgressInput {
  stage: ReportStage | null
  sectionsTotal: number
  sectionsDone: number
  elapsedMs: number
}

export function reportStageText(stage: ReportStage | null): string {
  return stage ? STAGE_TEXT[stage] : REPORT_PENDING_TEXT
}

/**
 * 目前進度百分比（0–99；100 只保留給 done）。
 *
 * writing 階段有章節分母時依 done/total 在區間內線性推進；沒有分母（退單次生成）則
 * 停在區間起點，由呼叫端改畫不定量進度條——**寧可誠實地不動，也不要編一個假的數字
 * 往前爬**，後者會在最後跳回真值。
 */
export function reportPct(input: ReportProgressInput): number {
  const { stage, sectionsTotal, sectionsDone } = input
  if (!stage) return 0
  const [lo, hi] = BANDS[stage]
  if (stage !== 'writing' && stage !== 'searching_web') return lo
  if (sectionsTotal <= 0) return lo
  const frac = Math.min(1, Math.max(0, sectionsDone / sectionsTotal))
  return Math.round(lo + (hi - lo) * frac)
}

/** writing 階段但沒有章節分母 → 進度條該畫成不定量掃光。 */
export function reportIsIndeterminate(input: ReportProgressInput): boolean {
  const { stage, sectionsTotal } = input
  return (stage === 'writing' || stage === 'searching_web') && sectionsTotal <= 0
}

/**
 * 預估剩餘毫秒；資料還不足以估算時回 null。
 *
 * 用的是「已耗時 ÷ 已完成比例」這個自我校正的估計式，而不是寫死的每節秒數：實際單節
 * 耗時受檢索命中數、是否觸發網搜、是否重生影響，差距可達數倍，寫死的常數只會給出一個
 * 有自信的錯誤答案。門檻 MIN_FRACTION 的作用是避開開頭那段分母極小、估計值會亂跳的區間
 * （2% 完成度時的外推可以差到十倍）。
 */
const MIN_FRACTION = 0.2
const MAX_ETA_MS = 30 * 60 * 1000

export function reportEtaMs(input: ReportProgressInput): number | null {
  const { elapsedMs } = input
  const pct = reportPct(input)
  const frac = pct / 100
  if (frac < MIN_FRACTION || elapsedMs <= 0) return null
  const remaining = (elapsedMs * (1 - frac)) / frac
  if (!Number.isFinite(remaining) || remaining <= 0) return null
  return Math.min(remaining, MAX_ETA_MS)
}

/** mm:ss（已耗時用，需要精確到秒才看得出「有在動」）。 */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const mm = Math.floor(total / 60)
  const ss = total % 60
  return `${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`
}

/**
 * 預估剩餘的人話。刻意粗粒度（分鐘、且未滿一分鐘一律說「不到 1 分鐘」）——估計值本身
 * 就有數十秒等級的誤差，報到秒只是假裝精準，而且會不停跳動更顯得不可靠。
 */
export function formatEta(ms: number | null): string | null {
  if (ms === null) return null
  const minutes = Math.ceil(ms / 60000)
  if (minutes <= 1) return '不到 1 分鐘'
  return `約 ${minutes} 分鐘`
}
