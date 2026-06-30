import type { Passage } from '../schemas'
import type { ReportItem, ReportResult } from '../schemas'

export interface Row extends ReportItem {
  rank?: number
  bestScore?: number
  matchCount?: number
  passages?: Passage[]
}

export const normalizeItem = (i: ReportItem): Row => ({ ...i })

export const normalizeResult = (r: ReportResult): Row => {
  const { rank, best_score, match_count, passages, ...rest } = r
  return { ...rest, rank, bestScore: best_score, matchCount: match_count, passages }
}
