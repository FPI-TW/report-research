import { describe, expect, it } from 'vitest'
import type { BrokerSummary } from '../../lib/radarSchemas'
import { brokerTableTsv } from './consensusExport'
import { epsBases } from './consensusSeries'

function broker(over: Partial<BrokerSummary>): BrokerSummary {
  return {
    broker: 'kgi', broker_display: '凱基', latest_report_date: '2026-09-10',
    latest_target_price: 1200, latest_target_currency: 'TWD',
    latest_eps_value: 45.2, latest_eps_fy: 2026, latest_eps_period: 'FY',
    latest_eps_currency: 'TWD', latest_eps_unit: null,
    ...over,
  } as BrokerSummary
}

function table(brokers: BrokerSummary[], currency: string | null = 'TWD') {
  const basis = epsBases(brokers)[0] ?? null
  return brokerTableTsv(brokers, currency, basis).split('\n').map(line => line.split('\t'))
}

describe('brokerTableTsv', () => {
  it('每列欄數與表頭一致，數值是原始數字而不是格式化字串', () => {
    const [header, row] = table([broker({})])
    expect(row).toHaveLength(header.length)
    expect(header.slice(0, 4)).toEqual(['券商', '目標價', '目標價幣別', '納入目標價口徑'])
    expect(row.slice(0, 4)).toEqual(['凱基', '1200', 'TWD', '是'])
    expect(row[header.indexOf('EPS')]).toBe('45.2')
    expect(row[header.indexOf('EPS 年度')]).toBe('2026')
    expect(row[header.indexOf('納入 EPS 口徑')]).toBe('是')
  })

  it('幣別或年度與目前口徑不同：數字照給，但標明未納入', () => {
    const rows = table([
      broker({}), broker({ broker: 'a', broker_display: '甲' }),
      broker({ broker: 'ms', broker_display: '大摩', latest_target_currency: 'USD', latest_eps_fy: 2027 }),
    ])
    const header = rows[0]
    const ms = rows.find(r => r[0] === '大摩')!
    expect(ms[header.indexOf('目標價')]).toBe('1200')
    expect(ms[header.indexOf('納入目標價口徑')]).toBe('否')
    expect(ms[header.indexOf('納入 EPS 口徑')]).toBe('否')
  })

  it('未標示幣別算未納入（與表格的判準相同），沒提供的值留空而不是寫 0', () => {
    const rows = table([
      broker({ latest_target_currency: null }),
      broker({ broker: 'x', broker_display: '乙', latest_target_price: null, latest_eps_value: null }),
    ])
    const header = rows[0]
    expect(rows[1][header.indexOf('納入目標價口徑')]).toBe('否')
    const empty = rows[2]
    for (const col of ['目標價', '目標價幣別', '納入目標價口徑', 'EPS', 'EPS 年度', '納入 EPS 口徑']) {
      expect(empty[header.indexOf(col)], col).toBe('')
    }
  })

  it('名稱裡的 tab 與換行不會讓整列錯位', () => {
    const [header, row] = table([broker({ broker_display: '凱基\t投顧\n研究部' })])
    expect(row).toHaveLength(header.length)
    expect(row[0]).toBe('凱基 投顧 研究部')
  })

  it('沒有券商時只有表頭', () => {
    expect(brokerTableTsv([], null, null).split('\n')).toHaveLength(1)
  })
})
