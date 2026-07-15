import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { ApiError } from '../../lib/api'
import { getBrokerHistory, getInstrumentRadar, getRadarInstruments } from '../../lib/radarApi'
import type { Window } from '../../lib/radarSchemas'

export function useRadarInstruments(opts: {
  market?: string
  q?: string
  enabled?: boolean
}) {
  const market = opts.market || undefined
  const q = opts.q?.trim() || undefined
  return useQuery({
    queryKey: ['radar-instruments', market ?? '', q ?? ''],
    queryFn: () => getRadarInstruments({ market, q, limit: 50 }),
    enabled: opts.enabled !== false,
    staleTime: 30_000,
    retry: false,
  })
}

export function useInstrumentRadar(
  code: string | null | undefined,
  market: string | null | undefined,
  window: Window,
) {
  const enabled = Boolean(code && market)
  return useQuery({
    queryKey: ['radar-overview', market ?? '', code ?? '', window],
    queryFn: () => getInstrumentRadar(code as string, market as string, window),
    enabled,
    placeholderData: keepPreviousData,
    retry: false,
  })
}

export function useBrokerHistory(
  code: string | null | undefined,
  broker: string | null | undefined,
  market: string | null | undefined,
  window: Window,
  expanded: boolean,
) {
  const enabled = Boolean(expanded && code && broker && market)
  return useQuery({
    queryKey: ['radar-broker', market ?? '', code ?? '', broker ?? '', window],
    queryFn: () => getBrokerHistory(code as string, broker as string, market as string, window),
    enabled,
    staleTime: 60_000,
    retry: false,
  })
}

/** 404 = 無此標的；其他錯誤留給 UI 重試。 */
export function isNotFound(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404
}
