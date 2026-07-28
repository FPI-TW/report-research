import { expect, test } from 'vitest'
import {
  formatElapsed,
  formatEta,
  reportEtaMs,
  reportIsIndeterminate,
  reportPct,
  reportStageText,
  type ReportProgressInput,
} from './reportProgress'

const base: ReportProgressInput = { stage: null, sectionsTotal: 0, sectionsDone: 0, elapsedMs: 0 }
const at = (over: Partial<ReportProgressInput>): ReportProgressInput => ({ ...base, ...over })

test('各階段落在遞增且不重疊的區間起點', () => {
  expect(reportPct(at({ stage: 'retrieving' }))).toBe(0)
  expect(reportPct(at({ stage: 'outlining' }))).toBe(12)
  expect(reportPct(at({ stage: 'verifying' }))).toBe(88)
  expect(reportPct(at({ stage: 'rendering' }))).toBe(94)
  expect(reportPct(at({ stage: null }))).toBe(0)
})

test('writing 依章節完成數在區間內推進', () => {
  const w = (done: number) => reportPct(at({ stage: 'writing', sectionsTotal: 8, sectionsDone: done }))
  expect(w(0)).toBe(18)
  expect(w(4)).toBe(53)
  expect(w(8)).toBe(88)
  // 逐節研報最長的一段必須真的會動；查表版整段固定 50% 正是「看起來沒在動」的成因。
  expect(w(1)).toBeGreaterThan(w(0))
  expect(w(7)).toBeLessThan(w(8))
})

test('searching_web 與 writing 共用區間，只換文案', () => {
  const shared = { sectionsTotal: 4, sectionsDone: 2 }
  expect(reportPct(at({ stage: 'searching_web', ...shared }))).toBe(
    reportPct(at({ stage: 'writing', ...shared })),
  )
  expect(reportStageText('searching_web')).not.toBe(reportStageText('writing'))
})

test('沒有章節分母時 writing 停在區間起點並標記為不定量', () => {
  const noOutline = at({ stage: 'writing', sectionsTotal: 0 })
  expect(reportPct(noOutline)).toBe(18)
  expect(reportIsIndeterminate(noOutline)).toBe(true)
  expect(reportIsIndeterminate(at({ stage: 'writing', sectionsTotal: 5 }))).toBe(false)
  expect(reportIsIndeterminate(at({ stage: 'rendering' }))).toBe(false)
})

test('完成數超出總數不會爆表', () => {
  expect(reportPct(at({ stage: 'writing', sectionsTotal: 3, sectionsDone: 9 }))).toBe(88)
})

test('ETA 在進度過低時不猜', () => {
  // 2% 完成度的外推誤差可達十倍，寧可不報。
  expect(reportEtaMs(at({ stage: 'retrieving', elapsedMs: 5000 }))).toBeNull()
  expect(reportEtaMs(at({ stage: 'writing', sectionsTotal: 8, sectionsDone: 0, elapsedMs: 5000 }))).toBeNull()
})

test('ETA 由已耗時除以已完成比例外推', () => {
  // 8 節寫完 4 節 → 53%，已耗時 5 分鐘 → 剩餘約 4.4 分鐘
  const eta = reportEtaMs(at({ stage: 'writing', sectionsTotal: 8, sectionsDone: 4, elapsedMs: 300_000 }))
  expect(eta).not.toBeNull()
  expect(Math.round((eta as number) / 1000)).toBe(266)
})

test('ETA 有上限，避免早期離群值報出荒謬數字', () => {
  const eta = reportEtaMs(at({ stage: 'writing', sectionsTotal: 20, sectionsDone: 4, elapsedMs: 3_600_000 }))
  expect(eta).toBe(30 * 60 * 1000)
})

test('已耗時格式化為 mm:ss', () => {
  expect(formatElapsed(0)).toBe('00:00')
  expect(formatElapsed(9_000)).toBe('00:09')
  expect(formatElapsed(125_000)).toBe('02:05')
  expect(formatElapsed(3_723_000)).toBe('62:03')
  expect(formatElapsed(-5)).toBe('00:00')
})

test('ETA 文案粗粒度到分鐘', () => {
  expect(formatEta(null)).toBeNull()
  expect(formatEta(20_000)).toBe('不到 1 分鐘')
  expect(formatEta(61_000)).toBe('約 2 分鐘')
  expect(formatEta(300_000)).toBe('約 5 分鐘')
})
