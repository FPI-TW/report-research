/** 監控面板速率行（純函式、無 DOM）。移植自 web/static/app/eta.js，行為須逐字相同。 */
export function rateText(remaining: number, rate: number | null, unit: string): string {
  if (rate == null) return '速率 計算中…'
  const line = `速率 ${rate.toFixed(1)} ${unit}/分`
  if (rate < 0.05) return line
  if (remaining <= 0) return `${line} · 已完成`
  const mins = remaining / rate
  const eta = mins < 90 ? `~${Math.round(mins)} 分` : `~${(mins / 60).toFixed(1)} 時`
  return `${line} · 預估剩餘 ${eta}`
}

export function ingestRateText(rpm: number | null, cps: number | null): string {
  if (rpm == null) return '速率 計算中…'
  const line = `速率 ${rpm.toFixed(1)} 篇/分`
  return cps == null ? line : `${line} · ${cps.toFixed(1)} 片段/秒`
}
