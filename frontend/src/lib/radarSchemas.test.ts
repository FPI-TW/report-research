import { describe, expect, it } from 'vitest'
import {
  brokerHistorySchema,
  radarInstrumentsSchema,
  radarOverviewSchema,
} from './radarSchemas'

const reportLink = {
  report_id: 'r1',
  file_name: 'a.pdf',
  report_date: '2026-07-11',
  broker: 'daiwa',
  broker_display: '大和',
}

describe('radarSchemas', () => {
  it('解析總覽 happy path', () => {
    const body = {
      market: 'TW',
      market_display: '台股',
      instrument_code: '8046',
      instrument_name: '南電',
      window: '90',
      as_of: '2026-07-11',
      coverage: {
        state: 'partial',
        brokers_total: 12,
        brokers_extracted: 1,
        brokers_in_consensus: 1,
        reports_available: 91,
        note: '部分擷取',
      },
      rating: {
        distribution: [{ rating: 'buy', count: 1 }],
        bullish: 1, neutral: 0, bearish: 0, unknown: 0,
        total_rated: 1, upgrades: 0, downgrades: 0, unchanged: 0,
      },
      target_price: {
        primary_currency: 'TWD',
        groups: [{
          currency: 'TWD', median: 2444, q1: 2444, q3: 2444,
          low: 2444, high: 2444, count: 1,
          revision_pct: null, revision_direction: 'none',
        }],
        note: null,
      },
      eps: { primary: null, groups: [] },
      thesis: [
        {
          dimension: 'outlook', dimension_display: '展望',
          label: 'insufficient', label_display: '資料不足',
          brokers_strengthen: 0, brokers_weaken: 0, brokers_comparable: 0,
        },
        {
          dimension: 'catalyst', dimension_display: '催化劑',
          label: 'insufficient', label_display: '資料不足',
          brokers_strengthen: 0, brokers_weaken: 0, brokers_comparable: 0,
        },
        {
          dimension: 'risk', dimension_display: '風險',
          label: 'insufficient', label_display: '資料不足',
          brokers_strengthen: 0, brokers_weaken: 0, brokers_comparable: 0,
        },
        {
          dimension: 'valuation', dimension_display: '估值',
          label: 'insufficient', label_display: '資料不足',
          brokers_strengthen: 0, brokers_weaken: 0, brokers_comparable: 0,
        },
      ],
      recent_events: [],
      recent_events_total: 0,
      brokers: [{
        broker: 'daiwa', broker_display: '大和',
        latest_rating: 'buy', latest_rating_raw: 'Buy',
        latest_target_price: 2444, latest_target_currency: 'TWD',
        latest_eps_value: null, latest_eps_fy: null,
        latest_report_date: '2026-07-11',
        report_link: reportLink,
        recent_change_label: null,
        recent_change_direction: 'none',
        stale: false, has_history: true,
      }],
      notes: [],
    }
    const parsed = radarOverviewSchema.parse(body)
    expect(parsed.instrument_code).toBe('8046')
    expect(parsed.thesis).toHaveLength(4)
    expect(parsed.rating?.bullish).toBe(1)
  })

  it('解析標的目錄', () => {
    const parsed = radarInstrumentsSchema.parse({
      total: 1, offset: 0,
      items: [{
        market: 'TW', market_display: '台股',
        instrument_code: '8046', instrument_name: '南電',
        broker_count: 12, report_count: 91,
        latest_report_date: '2026-07-11', coverage_state: 'partial',
      }],
    })
    expect(parsed.items[0].instrument_code).toBe('8046')
  })

  it('解析券商歷程', () => {
    const parsed = brokerHistorySchema.parse({
      market: 'TW', instrument_code: '8046',
      broker: 'daiwa', broker_display: '大和',
      window: 'all', as_of: '2026-07-11',
      current_rating: 'buy', report_count: 1,
      snapshots: [{
        report_id: 'r1', report_date: '2026-07-11', in_window: true,
        rating: 'buy', rating_raw: 'Buy',
        target_price: 2444, target_currency: 'TWD',
        eps: [], thesis: [], extraction_status: 'valid',
        report_link: reportLink,
      }],
      diffs: [{
        from_report_date: null, to_report_date: '2026-07-11',
        changes: [], has_prior_comparable: false, note: '無前次',
      }],
      coverage_state: 'partial',
    })
    expect(parsed.report_count).toBe(1)
    expect(parsed.diffs[0].has_prior_comparable).toBe(false)
  })
})
