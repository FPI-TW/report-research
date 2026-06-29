export interface MonitorRate {
  rpm: number | null
  cps: number | null
  spm: number | null
  tpm: number | null
}
export interface RateSample {
  reports: number
  chunks: number
  sumDone: number | null
  tagDone: number | null
}
export interface RateBase {
  reports: number
  chunks: number
  sum: number
  tag: number
  t: number
}

export const NULL_RATE: MonitorRate = { rpm: null, cps: null, spm: null, tpm: null }

// 與 web/static/app/monitor.html rate() 等價：開頁以來平均；首次建 base，dt<8 沿用上次。
export function nextRate(
  base: RateBase | null,
  last: MonitorRate,
  s: RateSample,
  nowMs: number,
): { base: RateBase; rate: MonitorRate } {
  if (!base) {
    return {
      base: { reports: s.reports, chunks: s.chunks, sum: s.sumDone ?? 0, tag: s.tagDone ?? 0, t: nowMs },
      rate: NULL_RATE,
    }
  }
  const dt = (nowMs - base.t) / 1000
  if (dt < 8) return { base, rate: last }
  return {
    base,
    rate: {
      rpm: ((s.reports - base.reports) / dt) * 60,
      cps: (s.chunks - base.chunks) / dt,
      spm: s.sumDone == null ? null : ((s.sumDone - base.sum) / dt) * 60,
      tpm: s.tagDone == null ? null : ((s.tagDone - base.tag) / dt) * 60,
    },
  }
}
