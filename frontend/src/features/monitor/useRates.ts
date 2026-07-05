import { useRef } from 'react'
import { computeRates, type Rates, type Baseline, type RateInputs } from './rate'
import type { Progress } from './progressSchema'

const NULL_RATES: Rates = { rpm: null, cps: null, spm: null, tpm: null }

/**
 * 開頁基準法：首次拿到 progress 時捕獲 baseline + t0（ref，掛載後不變＝「本次開頁」），
 * 其後每次回開頁至今平均速率。導覽離開再回來 → 元件重掛 → ref 重置 → 基準重來。
 */
export function useRates(progress: Progress | undefined): Rates {
  const base = useRef<{ b: Baseline; t: number } | null>(null)
  if (!progress) return NULL_RATES

  const cur: RateInputs = {
    reports: progress.db.reports,
    chunks: progress.db.chunks,
    sumDone: progress.summary?.done ?? null,
    tagDone: progress.tagging?.done ?? null,
  }
  // eslint-disable-next-line react-hooks/purity
  const now = Date.now()
  // eslint-disable-next-line react-hooks/refs
  if (!base.current) {
    base.current = {
      b: { reports: cur.reports, chunks: cur.chunks, sum: cur.sumDone ?? 0, tag: cur.tagDone ?? 0 },
      t: now,
    }
    return NULL_RATES
  }
  // eslint-disable-next-line react-hooks/refs
  return computeRates(cur, base.current.b, (now - base.current.t) / 1000)
}
