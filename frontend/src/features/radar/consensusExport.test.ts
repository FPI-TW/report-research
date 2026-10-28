import { describe, expect, it } from 'vitest'
import type { BrokerSummary, EpsGroup } from '../../lib/radarSchemas'
import { brokerTableTsv } from './consensusExport'

function broker(over: Partial<BrokerSummary>): BrokerSummary {
  return {
    broker: 'kgi', broker_display: '凱基', latest_rating: 'buy', latest_report_date: '2026-09-10',
    latest_target_price: 1200, latest_target_currency: 'TWD',
    latest_eps_value: 45.2, latest_eps_fy: 2026, latest_eps_period: 'FY',
    latest_eps_currency: 'TWD', latest_eps_unit: 'per_share',
    stale: false,
    ...over,
  } as BrokerSummary
}

const EPS: EpsGroup = {
  fiscal_year: 2026, period: 'FY', currency: 'TWD', unit: 'per_share',
  median: 45, count: 3, revision_direction: 'none',
}

function table(brokers: BrokerSummary[], currency: string | null = 'TWD', eps: EpsGroup | null = EPS) {
  return brokerTableTsv(brokers, currency, eps).split('\n').map(line => line.split('\t'))
}

describe('brokerTableTsv', () => {
  it('每列欄數與表頭一致，數值是原始數字而不是格式化字串', () => {
    const [header, row] = table([broker({})])
    expect(row).toHaveLength(header.length)
    expect(header.slice(0, 5)).toEqual(['券商', '評等', '目標價', '目標價幣別', '納入目標價共識'])
    expect(row.slice(0, 5)).toEqual(['凱基', '買進', '1200', 'TWD', '是'])
    expect(row[header.indexOf('EPS')]).toBe('45.2')
    expect(row[header.indexOf('EPS 年度')]).toBe('2026')
    expect(row[header.indexOf('與 EPS 共識同口徑')]).toBe('是')
    expect(row[header.indexOf('期間內')]).toBe('是')
  })

  it('幣別、年度不同或在期間外：數字照給，但標明未納入', () => {
    const rows = table([
      broker({}),
      broker({ broker: 'ms', broker_display: '大摩', latest_target_currency: 'USD', latest_eps_fy: 2027 }),
      broker({ broker: 'old', broker_display: '舊', stale: true }),
    ])
    const header = rows[0]
    const ms = rows.find(r => r[0] === '大摩')!
    expect(ms[header.indexOf('目標價')]).toBe('1200')
    expect(ms[header.indexOf('納入目標價共識')]).toBe('否')
    expect(ms[header.indexOf('與 EPS 共識同口徑')]).toBe('否')
    const old = rows.find(r => r[0] === '舊')!
    expect(old[header.indexOf('納入目標價共識')]).toBe('否')
    expect(old[header.indexOf('期間內')]).toBe('否')
  })

  it('沒有值的欄位留空，納入欄也留空（不是「否」）', () => {
    const [header, row] = table([broker({ latest_target_price: null, latest_eps_value: null })])
    expect(row[header.indexOf('目標價')]).toBe('')
    expect(row[header.indexOf('納入目標價共識')]).toBe('')
    expect(row[header.indexOf('與 EPS 共識同口徑')]).toBe('')
  })

  it('儲存格裡的 tab 與換行不會讓整列錯位', () => {
    const [header, row] = table([broker({ broker_display: '凱\t基\n證券' })])
    expect(row).toHaveLength(header.length)
    expect(row[0]).toBe('凱 基 證券')
  })
})
