import { afterEach, describe, expect, it, vi } from 'vitest'
import type { OpsServiceListResponse, OpsServiceStatus } from '../../lib/generated/adminApi'
import { CAPABILITIES_STORAGE_KEY, missingCapabilities, readStoredMissing, storeMissing } from './adminCapabilities'

const svc = (name: string): OpsServiceStatus => ({
  name, kind: 'systemd', tier: 'supporting', target: `report-mark-${name}.service`, timer: null, actions: ['status'],
  description: '', summary: 'idle', error: null, systemd: null, container: null, timer_state: null,
})
const list = (...names: string[]): OpsServiceListResponse =>
  ({ environment: 'staging', host: 'h', checked_at: '2026-10-13T06:00:00Z', items: names.map(svc) })

afterEach(() => {
  vi.restoreAllMocks()
  window.localStorage.clear()
})

describe('主機缺少的元件', () => {
  it('服務清單裡有監控匯入器：什麼都不缺', () => {
    expect(missingCapabilities(list('web', 'metrics', 'load-observations'))).toEqual([])
  })

  it('服務清單裡沒有監控匯入器（正式環境的 catalog）：缺 monitoring', () => {
    expect(missingCapabilities(list('web', 'nginx', 'sync', 'r2-reconcile'))).toEqual(['monitoring'])
    // 只有收集器、沒有匯入器，DB 一樣不會有資料
    expect(missingCapabilities(list('web', 'metrics'))).toEqual(['monitoring'])
  })

  it('清單還沒回來：不知道（null），不可以當成缺', () => {
    expect(missingCapabilities(undefined)).toBeNull()
  })

  it('上一次的判斷存在 localStorage；看不懂的內容與讀寫失敗都當成沒有', () => {
    expect(readStoredMissing()).toEqual([])
    storeMissing(['monitoring'])
    expect(readStoredMissing()).toEqual(['monitoring'])
    window.localStorage.setItem(CAPABILITIES_STORAGE_KEY, '["monitoring","bogus",3]')
    expect(readStoredMissing()).toEqual(['monitoring'])
    window.localStorage.setItem(CAPABILITIES_STORAGE_KEY, 'not json')
    expect(readStoredMissing()).toEqual([])
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    expect(readStoredMissing()).toEqual([])
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
    expect(() => storeMissing([])).not.toThrow()
  })
})
