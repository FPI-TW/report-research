import type { ComponentType } from 'react'

export type RouteKey = 'search' | 'ask' | 'monitor' | 'radar' | 'help'

/** lazy() 與預載共用同一組 import thunk（單一真相，避免路徑字串重複） */
export const routeLoaders: Record<RouteKey, () => Promise<{ default: ComponentType }>> = {
  search: () => import('../features/search/SearchPage'),
  ask: () => import('../features/ask/AskPage'),
  monitor: () => import('../features/monitor/MonitorPage'),
  radar: () => import('../features/radar/RadarPage'),
  help: () => import('../features/help/HelpPage'),
}

const started = new Set<RouteKey>()

/** 觸發對向路由 chunk 預載；同一 key 只跑一次；失敗時清除狀態以允許之後重試 */
export function preloadRoute(key: RouteKey): void {
  if (started.has(key)) return
  started.add(key)
  routeLoaders[key]().catch(() => {
    started.delete(key)
  })
}

/** 測試用：重置預載狀態，避免測試間互相汙染 */
export function __resetPreloadState(): void {
  started.clear()
}

/** 閒置時批次預載（requestIdleCallback → 退回 setTimeout） */
export function preloadIdle(keys: RouteKey[]): void {
  const run = () => keys.forEach(preloadRoute)
  if (typeof requestIdleCallback === 'function') requestIdleCallback(run)
  else setTimeout(run, 200)
}
