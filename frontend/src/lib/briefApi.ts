import { getJSON } from './api'
import { briefEnvelopeSchema, type BriefEnvelope } from './briefSchemas'

/** 最新一份簡報。沒有任何簡報時回 status="pending"（200，不是 404）。 */
export function getLatestBrief(): Promise<BriefEnvelope> {
  return getJSON('/api/brief/latest', briefEnvelopeSchema, { cache: 'no-store' })
}

/** 指定日期的簡報。查不到是真的 404（ApiError），由呼叫端呈現。 */
export function getBriefByDate(date: string): Promise<BriefEnvelope> {
  return getJSON(`/api/brief/${encodeURIComponent(date)}`, briefEnvelopeSchema, {
    cache: 'no-store',
  })
}
