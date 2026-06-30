import { test, expect } from 'vitest'
import { targetsSummary } from './targets'
import type { Row } from '../lib/normalize'

const mk = (over: Partial<Row>): Row =>
  ({ report_id: 'x', file_name: 'f', ...over }) as Row

test('無標的回 —', () => {
  expect(targetsSummary(mk({}))).toBe('—')
})

test('個股列前三檔、超過加 …', () => {
  expect(
    targetsSummary(mk({ relates_stock: true, stock_targets: ['2330', '2317', '2454', '3008'] })),
  ).toBe('個股 2330、2317、2454…')
})

test('個股無清單回「個股」', () => {
  expect(targetsSummary(mk({ relates_stock: true, stock_targets: null }))).toBe('個股')
})

test('個股 + 期貨以全形空格相接', () => {
  expect(
    targetsSummary(mk({ relates_stock: true, stock_targets: ['2330'], relates_futures: true, futures_targets: ['TX'] })),
  ).toBe('個股 2330　期貨 TX')
})
