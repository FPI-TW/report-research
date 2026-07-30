import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ScheduleHealthPanel } from './ScheduleHealthPanel'
import type { LogEntry, UnitFailures } from './progressSchema'

const sync: LogEntry = {
  raw: '[2026-07-30 09:04:12] === sync done ===',
  timestamp: '2026-07-30 09:04:12',
  status: 'done',
  label: '同步已完成',
}

const failures: UnitFailures = {
  latest: '2026-07-28T15:00:03+08:00',
  count_24h: 2,
  count_7d: 10,
  recent: [
    { ts: '2026-07-28T15:00:03+08:00', unit: 'report-mark-sync.service', stage: 'sync_new_reports(import)', rc: 2 },
    { ts: '2026-07-28T12:00:04+08:00', unit: 'report-mark-web.service', stage: null, rc: null },
  ],
}

const quiet: UnitFailures = { latest: null, count_24h: 0, count_7d: 0, recent: [] }

test('同步狀態與最近失敗都進 DOM', () => {
  render(<ScheduleHealthPanel sync={sync} failures={failures} />)
  expect(screen.getByText('排程健康')).toBeInTheDocument()
  expect(screen.getByText('同步已完成')).toBeInTheDocument()
  expect(screen.getByText('2026-07-30 09:04:12')).toBeInTheDocument()
  expect(screen.getByText('近 24 小時 2 筆')).toBeInTheDocument()
  expect(screen.getByText('近 7 日 10 筆')).toBeInTheDocument()
  expect(screen.getByText('report-mark-sync.service · sync_new_reports(import) · rc=2')).toBeInTheDocument()
})

test('近 24 小時有失敗 → 亮紅點且計數標警示色', () => {
  const { container } = render(<ScheduleHealthPanel sync={sync} failures={failures} />)
  expect(container.querySelector('[class*="alertDot"]')).not.toBeNull()
  const warn = [...container.querySelectorAll('[class*="fWarn"]')].map(e => e.textContent)
  expect(warn).toContain('近 24 小時 2 筆')
})

test('近 24 小時零失敗 → 不亮紅點（否則紅點上線第一天就永遠亮著）', () => {
  // 這個檔是 append-only、沒有 logrotate、也沒有已讀游標，所以紅點條件只能是
  // 時間窗；用累計筆數會讓紅點變成背景噪音，兩週內就沒人看。
  const { container } = render(<ScheduleHealthPanel sync={sync} failures={quiet} />)
  expect(container.querySelector('[class*="alertDot"]')).toBeNull()
  expect(screen.getByText('最後失敗 —')).toBeInTheDocument()
})

test('近 7 日有、近 24 小時無 → 不亮紅點但仍看得到歷史', () => {
  const cooled: UnitFailures = { ...failures, count_24h: 0 }
  const { container } = render(<ScheduleHealthPanel sync={sync} failures={cooled} />)
  expect(container.querySelector('[class*="alertDot"]')).toBeNull()
  expect(screen.getByText('近 7 日 10 筆')).toBeInTheDocument()
})

test('沒有 sync 紀錄 → 降級文案，不是空白列', () => {
  render(<ScheduleHealthPanel sync={null} failures={quiet} />)
  expect(screen.getByText('尚無同步紀錄')).toBeInTheDocument()
})

test('後端未提供 unit_failures → 降級文案，整張卡不消失', () => {
  // 滾動部署期間前端可能先上線；缺一塊不該讓維運看不到同步狀態。
  render(<ScheduleHealthPanel sync={sync} failures={undefined} />)
  expect(screen.getByText('此版後端未提供失敗紀錄')).toBeInTheDocument()
  expect(screen.getByText('同步已完成')).toBeInTheDocument()
})

test('時間戳只顯示到分（ISO 帶時區偏移，秒沒有意義）', () => {
  render(<ScheduleHealthPanel sync={sync} failures={failures} />)
  expect(screen.getByText('最後失敗 2026-07-28 15:00')).toBeInTheDocument()
})
