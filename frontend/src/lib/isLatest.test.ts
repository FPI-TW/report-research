import { describe, it, expect } from 'vitest'
import { latestId } from './isLatest'

const r = (report_id: string, report_date: string | null) => ({ report_id, report_date })

describe('latestId (mirror answer.py)', () => {
  it('嚴格最大者的 id', () => {
    expect(latestId([r('a', '2026-05-01'), r('b', '2026-06-25'), r('c', '2026-06-01')])).toBe('b')
  })
  it('同日保留最先出現者（嚴格大於）', () => {
    expect(latestId([r('a', '2026-06-25'), r('b', '2026-06-25')])).toBe('a')
  })
  it('無日期不參與；全無日期→null', () => {
    expect(latestId([r('a', null), r('b', '2026-06-01')])).toBe('b')
    expect(latestId([r('a', null), r('b', null)])).toBeNull()
    expect(latestId([])).toBeNull()
  })
})
