import { getJSON } from '../../lib/api'
import {
  reportsSchema,
  searchSchema,
  statsSchema,
  type ReportsResponse,
  type SearchResponse,
  type StatsResponse,
} from './schemas'

export interface ReportsParams {
  market?: string
  instrument_type?: string
  relates_stock?: boolean
  relates_futures?: boolean
  report_type?: string
  sort?: string
  limit: number
  offset: number
}

export interface SearchParams extends ReportsParams {
  q: string
  passages: number
}

const SKIP = (v: unknown) => v == null || v === '' || v === '全部'

export function buildQuery(params: Record<string, unknown>): string {
  const u = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (SKIP(v)) continue
    u.set(k, String(v))
  }
  return u.toString()
}

export function getStats(): Promise<StatsResponse> {
  return getJSON('/api/stats', statsSchema, { cache: 'no-store' })
}

export function getReports(p: ReportsParams): Promise<ReportsResponse> {
  return getJSON(`/api/reports?${buildQuery({ ...p })}`, reportsSchema)
}

export function getSearch(p: SearchParams): Promise<SearchResponse> {
  return getJSON(`/api/search?${buildQuery({ ...p })}`, searchSchema)
}
