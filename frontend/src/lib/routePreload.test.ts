import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import * as rp from './routePreload'

beforeEach(() => {
  rp.__resetPreloadState()
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

test('preloadRoute 首次呼叫觸發 loader、重複呼叫去重', () => {
  const spy = vi.spyOn(rp.routeLoaders, 'ask').mockResolvedValue({ default: () => null } as never)
  rp.preloadRoute('ask')
  rp.preloadRoute('ask')
  expect(spy).toHaveBeenCalledTimes(1)
})

test('preloadIdle 於 requestIdleCallback 可用時排程並預載', () => {
  const spy = vi.spyOn(rp.routeLoaders, 'search').mockResolvedValue({ default: () => null } as never)
  const ric = vi.fn((cb: () => void) => { cb(); return 0 })
  vi.stubGlobal('requestIdleCallback', ric)
  rp.preloadIdle(['search'])
  expect(ric).toHaveBeenCalledTimes(1)
  expect(spy).toHaveBeenCalledTimes(1)
})

test('preloadIdle 無 requestIdleCallback 時退回 setTimeout', () => {
  vi.stubGlobal('requestIdleCallback', undefined)
  vi.useFakeTimers()
  const spy = vi.spyOn(rp.routeLoaders, 'monitor').mockResolvedValue({ default: () => null } as never)
  rp.preloadIdle(['monitor'])
  vi.runAllTimers()
  expect(spy).toHaveBeenCalledTimes(1)
  vi.useRealTimers()
})

test('preloadRoute 首次載入失敗後清除狀態，之後可重試', async () => {
  let calls = 0
  const spy = vi.spyOn(rp.routeLoaders, 'ask').mockImplementation(() => {
    calls += 1
    return calls === 1 ? Promise.reject(new Error('boom')) : Promise.resolve({ default: () => null } as never)
  })
  rp.preloadRoute('ask')
  await Promise.resolve() // 讓 rejection 的 .catch 跑完、清除 started
  await Promise.resolve()
  rp.preloadRoute('ask')
  expect(spy).toHaveBeenCalledTimes(2)
})
