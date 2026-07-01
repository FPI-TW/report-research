import { describe, expect, test } from 'vitest'
import {
  kpiItemSchema,
  kpiBlockSchema,
  chartSeriesSchema,
  chartBlockSchema,
  reportSummarySchema,
  reportFullSchema,
  historyItemSchema,
} from './schemas'

describe('schemas - kpi', () => {
  test('kpiBlockSchema 接受合法資料', () => {
    const valid = {
      items: [
        { label: 'Revenue', value: '100M', change: '+5%', dir: 'up', source: 'Q1 Report' },
        { label: 'Margin', value: '20%', change: null, dir: null, source: null },
      ],
    }
    expect(kpiBlockSchema.parse(valid).items).toHaveLength(2)
  })

  test('kpiBlockSchema 缺 items 預設空陣列', () => {
    const result = kpiBlockSchema.parse({})
    expect(result.items).toEqual([])
  })

  test('kpiItemSchema 標籤預設空字串', () => {
    const result = kpiItemSchema.parse({ value: '123' })
    expect(result.label).toBe('')
    expect(result.value).toBe('123')
  })

  test('kpiItemSchema 值預設空字串', () => {
    const result = kpiItemSchema.parse({ label: 'Test' })
    expect(result.label).toBe('Test')
    expect(result.value).toBe('')
  })

  test('kpiBlockSchema 容忍缺欄（change/dir/source）', () => {
    const valid = { items: [{ label: 'X', value: 'Y' }] }
    expect(() => kpiBlockSchema.parse(valid)).not.toThrow()
  })
})

describe('schemas - chart', () => {
  test('chartBlockSchema 接受 bar 型', () => {
    const valid = {
      type: 'bar',
      title: 'Revenue',
      x: ['2024', '2025'],
      series: [{ name: 'Sales', values: [100, 200] }],
      unit: 'M',
      source: 'Report',
    }
    expect(chartBlockSchema.parse(valid).type).toBe('bar')
  })

  test('chartBlockSchema 接受 line 型', () => {
    const valid = {
      type: 'line',
      x: [1, 2, 3],
      series: [{ name: 'Trend', values: [10, 20, 30] }],
    }
    expect(chartBlockSchema.parse(valid).type).toBe('line')
  })

  test('chartBlockSchema 接受 pie 型', () => {
    const valid = {
      type: 'pie',
      series: [{ name: 'Segment A', values: [50] }],
    }
    expect(chartBlockSchema.parse(valid).type).toBe('pie')
  })

  test('chartBlockSchema 拒絕未知 type', () => {
    const invalid = { type: 'unknown', x: [], series: [] }
    const result = chartBlockSchema.safeParse(invalid)
    expect(result.success).toBe(false)
  })

  test('chartBlockSchema 預設 x 和 series 為空陣列', () => {
    const result = chartBlockSchema.parse({ type: 'bar' })
    expect(result.x).toEqual([])
    expect(result.series).toEqual([])
  })

  test('chartSeriesSchema 預設名稱空字串', () => {
    const result = chartSeriesSchema.parse({ values: [1, 2, 3] })
    expect(result.name).toBe('')
    expect(result.values).toEqual([1, 2, 3])
  })
})

describe('schemas - report', () => {
  test('reportFullSchema markdown 預設空字串', () => {
    const valid = { report_id: 'r1', title: 'Test Report' }
    const result = reportFullSchema.parse(valid)
    expect(result.markdown).toBe('')
  })

  test('reportFullSchema 接受完整資料', () => {
    const valid = {
      report_id: 'r1',
      title: 'Deep Report',
      markdown: '# Title\n\nContent here',
    }
    expect(reportFullSchema.parse(valid).markdown).toContain('# Title')
  })

  test('reportFullSchema 容忍 null title', () => {
    const valid = { report_id: 'r1', title: null, markdown: 'Text' }
    expect(() => reportFullSchema.parse(valid)).not.toThrow()
  })

  test('reportSummarySchema 接受合法資料', () => {
    const valid = {
      report_id: 'r1',
      title: 'Summary',
      download_url: '/download/r1',
      created_at: '2026-01-01T00:00:00Z',
    }
    expect(reportSummarySchema.parse(valid).report_id).toBe('r1')
  })
})

describe('schemas - historyItem with reports', () => {
  test('historyItemSchema 容忍 reports:null', () => {
    const valid = {
      id: 'q1',
      question: 'What?',
      reports: null,
    }
    expect(() => historyItemSchema.parse(valid)).not.toThrow()
  })

  test('historyItemSchema 接受 reports 陣列', () => {
    const valid = {
      id: 'q1',
      question: 'What?',
      reports: [
        {
          report_id: 'r1',
          title: 'Report 1',
          download_url: '/dl/r1',
          created_at: '2026-01-01T00:00:00Z',
        },
      ],
    }
    const result = historyItemSchema.parse(valid)
    expect(result.reports).toHaveLength(1)
    expect(result.reports?.[0].report_id).toBe('r1')
  })

  test('historyItemSchema 缺 reports 也過', () => {
    const valid = {
      id: 'q1',
      question: 'What?',
    }
    expect(() => historyItemSchema.parse(valid)).not.toThrow()
  })
})
