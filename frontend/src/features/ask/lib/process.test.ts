import { describe, expect, test } from 'vitest'
import { deriveProcess, thinkingLabel } from './process'
import { emptyTurn } from './conversation'

const turnAt = (over: Partial<ReturnType<typeof emptyTurn>>) => ({ ...emptyTurn('t', 'q'), ...over })

describe('thinkingLabel', () => {
  test('ms 轉秒、至少 1 秒', () => {
    expect(thinkingLabel(2400)).toBe('已思考 2 秒')
    expect(thinkingLabel(200)).toBe('已思考 1 秒')
  })
  test('無效回 null', () => {
    expect(thinkingLabel(null)).toBeNull()
  })
})

describe('deriveProcess', () => {
  test('understanding：understand 活躍其餘 pending', () => {
    const { steps } = deriveProcess(turnAt({ stage: 'understanding' }))
    expect(steps.find((s) => s.key === 'understand')?.state).toBe('active')
    expect(steps.find((s) => s.key === 'reading')?.state).toBe('pending')
  })
  test('retrieved：understand/retrieved done、reading active、retrieved 標籤含篇數', () => {
    const { steps } = deriveProcess(
      turnAt({ stage: 'retrieved', sources: [{ n: 1, report_id: 'r', file_name: 'f', market: null, report_date: null, is_latest: false }] }),
    )
    expect(steps.find((s) => s.key === 'understand')?.state).toBe('done')
    expect(steps.find((s) => s.key === 'retrieved')?.state).toBe('done')
    expect(steps.find((s) => s.key === 'retrieved')?.label).toBe('找到 1 篇相關研報')
    expect(steps.find((s) => s.key === 'reading')?.state).toBe('active')
  })
  test('web 步驟僅 webUsed 顯示', () => {
    expect(deriveProcess(turnAt({})).steps.find((s) => s.key === 'web')?.hidden).toBe(true)
    expect(deriveProcess(turnAt({ webUsed: true })).steps.find((s) => s.key === 'web')?.hidden).toBe(false)
  })
  test('phase done：全部 done + 思考秒數標題', () => {
    const { steps, headDone, headLabel } = deriveProcess(turnAt({ phase: 'done', thinkingMs: 1500 }))
    expect(steps.every((s) => s.state === 'done')).toBe(true)
    expect(headDone).toBe(true)
    expect(headLabel).toBe('已思考 2 秒')
  })
})
