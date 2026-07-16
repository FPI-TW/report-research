import { getJSON } from './api'
import {
  brokerHistorySchema,
  radarInstrumentsSchema,
  radarOverviewSchema,
  type BrokerHistory,
  type RadarInstruments,
  type RadarOverview,
  type Window,
} from './radarSchemas'

export function getRadarInstruments(params: {
  market?: string
  q?: string
  limit?: number
  offset?: number
}): Promise<RadarInstruments> {
  const sp = new URLSearchParams()
  if (params.market) sp.set('market', params.market)
  if (params.q) sp.set('q', params.q)
  if (params.limit != null) sp.set('limit', String(params.limit))
  if (params.offset != null) sp.set('offset', String(params.offset))
  const qs = sp.toString()
  return getJSON(`/api/radar/instruments${qs ? `?${qs}` : ''}`, radarInstrumentsSchema, {
    cache: 'no-store',
  })
}

export function getInstrumentRadar(
  code: string,
  market: string,
  window: Window = '90',
): Promise<RadarOverview> {
  const sp = new URLSearchParams({ market, window })
  return getJSON(
    `/api/instrument/${encodeURIComponent(code)}/radar?${sp}`,
    radarOverviewSchema,
    { cache: 'no-store' },
  )
}

export function getBrokerHistory(
  code: string,
  broker: string,
  market: string,
  window: Window = '90',
): Promise<BrokerHistory> {
  const sp = new URLSearchParams({ market, window })
  return getJSON(
    `/api/instrument/${encodeURIComponent(code)}/radar/brokers/${encodeURIComponent(broker)}?${sp}`,
    brokerHistorySchema,
    { cache: 'no-store' },
  )
}
