import { getJSON } from './api'
import { ratingChangesSchema, type RatingChanges } from './librarySchemas'

/** 跨標的的最新評等變動（研報庫首頁）。 */
export function getRatingChanges(limit = 4): Promise<RatingChanges> {
  const sp = new URLSearchParams({ limit: String(limit) })
  return getJSON(`/api/radar/rating-changes?${sp}`, ratingChangesSchema, { cache: 'no-store' })
}
