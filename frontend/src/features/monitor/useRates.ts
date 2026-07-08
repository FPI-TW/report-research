import { useRef } from 'react'
import { computeRates, type Rates, type Baseline, type RateInputs } from './rate'
import type { Progress } from './progressSchema'

const NULL_RATES: Rates = { rpm: null, cps: null, spm: null, tpm: null }

/**
 * 開頁基準法：首次拿到 progress 時捕獲 baseline + t0（ref，掛載後不變＝「本次開頁」），
 * 其後每次回開頁至今平均速率。`now` 由呼叫端傳入 react-query 的 dataUpdatedAt（每 poll 才變），
 * 故時鐘每秒 re-render 期間 now 不變→速率穩定不抖動；導覽離開再回來→元件重掛→ref 重置→基準重來。
 */
export function useRates(progress: Progress | undefined, now: number): Rates {
  // ref 存本次開頁基準：僅在 render 讀寫此非反應式 baseline（首值捕獲慣用法），
  // 由 !base.current 冪等守門，重複 render 不會覆寫→安全。
  const base = useRef<{ b: Baseline; t: number } | null>(null)
  if (!progress) return NULL_RATES

  const cur: RateInputs = {
    reports: progress.db.reports,
    chunks: progress.db.chunks,
    sumDone: progress.summary?.done ?? null,
    tagDone: progress.tagging?.done ?? null,
  }
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
