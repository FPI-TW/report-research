export interface Rates {
  rpm: number | null
  cps: number | null
  spm: number | null
  tpm: number | null
}
export interface RateInputs {
  reports: number
  chunks: number
  sumDone: number | null
  tagDone: number | null
}
export interface Baseline {
  reports: number
  chunks: number
  sum: number
  tag: number
}

const NULL_RATES: Rates = { rpm: null, cps: null, spm: null, tpm: null }

/**
 * 開頁基準法（移植 vanilla rate()）：baseline 於首筆由呼叫端捕獲，本函式只做
 * 「開頁至今平均速率」的算術 + 8 秒暖機。elapsedSec<8 → 全 null（暖機期間
 * vanilla 回 lastRate，因該期尚無非 null 值故等價）。
 */
export function computeRates(cur: RateInputs, base: Baseline, elapsedSec: number): Rates {
  if (elapsedSec < 8) return NULL_RATES
  return {
    rpm: ((cur.reports - base.reports) / elapsedSec) * 60,
    cps: (cur.chunks - base.chunks) / elapsedSec,
    spm: cur.sumDone == null ? null : ((cur.sumDone - base.sum) / elapsedSec) * 60,
    tpm: cur.tagDone == null ? null : ((cur.tagDone - base.tag) / elapsedSec) * 60,
  }
}

/** 摘要/標註面板速率行（移植 eta.js）。remaining＝剩餘數，unit 例「摘要」「標註」。 */
export function rateText(remaining: number, rate: number | null, unit: string): string {
  if (rate == null) return '速率 計算中…'
  const line = `速率 ${rate.toFixed(1)} ${unit}/分`
  if (rate < 0.05) return line
  if (remaining <= 0) return `${line} · 已完成`
  const mins = remaining / rate
  const eta = mins < 90 ? `~${Math.round(mins)} 分` : `~${(mins / 60).toFixed(1)} 時`
  return `${line} · 預估剩餘 ${eta}`
}

/** 導入面板速率行（移植 eta.js）：無已知總量、不給 ETA，第二段放每秒片段數。 */
export function ingestRateText(rpm: number | null, cps: number | null): string {
  if (rpm == null) return '速率 計算中…'
  const line = `速率 ${rpm.toFixed(1)} 篇/分`
  return cps == null ? line : `${line} · ${cps.toFixed(1)} 片段/秒`
}

/** 千分位整數格式（穩定用 en-US 逗號）。 */
export function fmtInt(n: number): string {
  return n.toLocaleString('en-US')
}
