import { getJSON } from './api'
import {
  brokerHistorySchema,
  radarEventsSchema,
  radarInstrumentsSchema,
  radarOverviewSchema,
  type BrokerHistory,
  type CatalogSort,
  type Market,
  type StanceFilter,
  type RadarEvents,
  type RadarInstruments,
  type RadarOverview,
  type Window,
} from './radarSchemas'

export interface RadarRequestOptions {
  signal?: AbortSignal
}

export function getRadarInstruments(params: {
  market?: Market
  q?: string
  sort?: CatalogSort
  stance?: StanceFilter
  limit?: number
  offset?: number
}, options: RadarRequestOptions = {}): Promise<RadarInstruments> {
  const sp = new URLSearchParams()
  if (params.market) sp.set('market', params.market)
  if (params.q) sp.set('q', params.q)
  if (params.sort) sp.set('sort', params.sort)
  if (params.stance) sp.set('stance', params.stance)
  if (params.limit != null) sp.set('limit', String(params.limit))
  if (params.offset != null) sp.set('offset', String(params.offset))
  const qs = sp.toString()
  return getJSON(`/api/radar/instruments${qs ? `?${qs}` : ''}`, radarInstrumentsSchema, {
    cache: 'no-store',
    signal: options.signal,
  })
}

export function getInstrumentRadar(
  code: string,
  market: Market,
  window: Window = '90',
  options: RadarRequestOptions = {},
): Promise<RadarOverview> {
  const sp = new URLSearchParams({ market, window })
  return getJSON(
    `/api/instrument/${encodeURIComponent(code)}/radar?${sp}`,
    radarOverviewSchema,
    { cache: 'no-store', signal: options.signal },
  )
}

export function getBrokerHistory(
  code: string,
  broker: string,
  market: Market,
  window: Window = '90',
  options: RadarRequestOptions = {},
): Promise<BrokerHistory> {
  const sp = new URLSearchParams({ market, window })
  return getJSON(
    `/api/instrument/${encodeURIComponent(code)}/radar/brokers/${encodeURIComponent(broker)}?${sp}`,
    brokerHistorySchema,
    { cache: 'no-store', signal: options.signal },
  )
}

export function getRadarEvents(
  code: string,
  params: { market: Market; window: Window; limit?: number; offset?: number },
  options: RadarRequestOptions = {},
): Promise<RadarEvents> {
  const sp = new URLSearchParams({ market: params.market, window: params.window })
  if (params.limit != null) sp.set('limit', String(params.limit))
  if (params.offset != null) sp.set('offset', String(params.offset))
  return getJSON(
    `/api/instrument/${encodeURIComponent(code)}/radar/events?${sp}`,
    radarEventsSchema,
    { cache: 'no-store', signal: options.signal },
  )
}
