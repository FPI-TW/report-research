import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { ApiError } from '../../lib/api'
import {
  getBrokerHistory,
  getInstrumentRadar,
  getRadarEvents,
  getRadarInstruments,
} from '../../lib/radarApi'
import type {
  CatalogSort,
  Market,
  RadarEvents,
  RadarInstruments,
  StanceFilter,
  Window,
} from '../../lib/radarSchemas'

const PAGE_SIZE = 50
const EVENT_PAGE_SIZE = 12
/** 與後端 `CatalogSort` 的預設值相同；只用來決定「要不要把它送出去」。 */
const DEFAULT_SORT: CatalogSort = 'latest'

function nextCatalogOffset(page: RadarInstruments): number | undefined {
  if (page.has_more === false) return undefined
  if (page.next_offset != null) return page.next_offset
  const next = page.offset + page.items.length
  return page.items.length > 0 && next < page.total ? next : undefined
}

function nextEventOffset(page: RadarEvents): number | undefined {
  return page.has_more && page.next_offset != null ? page.next_offset : undefined
}

export function useRadarInstruments(opts: {
  market?: Market
  q?: string
  sort?: CatalogSort
  stance?: StanceFilter
  enabled?: boolean
}) {
  const market = opts.market || undefined
  const q = opts.q?.trim() || undefined
  // 預設排序不進網址也不進請求：後端的 `sort` 預設就是 `latest`（`web/routers/radar.py`
  // 的 `Query("latest")`），送與不送逐字等價，而不送能讓網址與請求都維持既有形狀。
  const sort = opts.sort && opts.sort !== DEFAULT_SORT ? opts.sort : undefined
  const stance = opts.stance || undefined
  const query = useInfiniteQuery({
    // sort／stance 必須進 queryKey：漏掉的話換排序會命中同一筆快取，畫面一動也不動、
    // network 也不會有請求（staleTime 30s 讓這個誤判更頑固），看起來就像後端沒支援。
    queryKey: ['radar-instruments', market ?? '', q ?? '', sort ?? '', stance ?? ''],
    queryFn: ({ pageParam, signal }) => getRadarInstruments(
      { market, q, sort, stance, limit: PAGE_SIZE, offset: pageParam },
      { signal },
    ),
    initialPageParam: 0,
    getNextPageParam: nextCatalogOffset,
    enabled: opts.enabled !== false,
    staleTime: 30_000,
    retry: false,
  })
  const pages = query.data?.pages
  const firstPage = pages?.[0]
  const lastPage = pages?.at(-1)
  const data = firstPage && lastPage
    ? {
        ...firstPage,
        total: lastPage.total,
        has_more: lastPage.has_more,
        next_offset: lastPage.next_offset,
        items: pages.flatMap(page => page.items),
      }
    : undefined
  return {
    ...query,
    data,
    hasMore: query.hasNextPage,
    remaining: data ? Math.max(0, data.total - data.items.length) : 0,
    loadMore: query.fetchNextPage,
    isFetchingMore: query.isFetchingNextPage,
  }
}

export function useInstrumentRadar(
  code: string | null | undefined,
  market: Market | null | undefined,
  window: Window,
) {
  const enabled = Boolean(code && market)
  return useQuery({
    queryKey: ['radar-overview', market ?? '', code ?? '', window],
    queryFn: ({ signal }) => getInstrumentRadar(
      code as string,
      market as Market,
      window,
      { signal },
    ),
    enabled,
    retry: false,
  })
}

export function useBrokerHistory(
  code: string | null | undefined,
  broker: string | null | undefined,
  market: Market | null | undefined,
  window: Window,
  expanded: boolean,
) {
  const enabled = Boolean(expanded && code && broker && market)
  return useQuery({
    queryKey: ['radar-broker', market ?? '', code ?? '', broker ?? '', window],
    queryFn: ({ signal }) => getBrokerHistory(
      code as string,
      broker as string,
      market as Market,
      window,
      { signal },
    ),
    enabled,
    staleTime: 60_000,
    retry: false,
  })
}

export function useRadarEvents(
  code: string | null | undefined,
  market: Market | null | undefined,
  window: Window,
  enabled: boolean,
) {
  const query = useInfiniteQuery({
    queryKey: ['radar-events', market ?? '', code ?? '', window],
    queryFn: ({ pageParam, signal }) => getRadarEvents(
      code as string,
      { market: market as Market, window, limit: EVENT_PAGE_SIZE, offset: pageParam },
      { signal },
    ),
    initialPageParam: 0,
    getNextPageParam: nextEventOffset,
    enabled: Boolean(enabled && code && market),
    retry: false,
  })
  const pages = query.data?.pages
  const firstPage = pages?.[0]
  const lastPage = pages?.at(-1)
  const data = firstPage && lastPage
    ? {
        ...firstPage,
        total: lastPage.total,
        has_more: lastPage.has_more,
        next_offset: lastPage.next_offset,
        items: pages.flatMap(page => page.items),
      }
    : undefined
  return {
    ...query,
    data,
    hasMore: query.hasNextPage,
    remaining: data ? Math.max(0, data.total - data.items.length) : 0,
    loadMore: query.fetchNextPage,
    isFetchingMore: query.isFetchingNextPage,
  }
}

/** 404 = 無此標的；其他錯誤留給 UI 重試。 */
export function isNotFound(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404
}
