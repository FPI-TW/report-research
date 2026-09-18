/**
 * SSE 事件契約守門（前端側）。
 *
 * 吃的是 repo 根的 `tests/fixtures/sse_events.json`——後端的
 * `tests/test_sse_event_contract.py` 吃同一份。後端那側證明「fixture 沒漏掉任何後端會送
 * 的事件種類」，這一側證明「fixture 裡的每一種都真的解得出來」。兩側都綠才等於契約成立。
 *
 * 為什麼需要：`parseAskEvent` 對未宣告的 event 一律回 `null` 而被靜默丟棄。已移除的深度
 * 研報功能曾有 `section_draft` 從 M7 起就在送、前端沒有 case，症狀只是「進度條停在 50%
 * 不動」，撐了好幾個里程碑沒有任何錯誤訊息。在此之前沒有任何測試在釘這件事。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import fixture from '@fixtures/sse_events.json'
import { parseAskEvent } from './askSchemas'

type ContractEntry = {
  event: string
  origin: string[]
  frontend: 'parsed' | 'ignored'
  data: unknown
  note?: string
}

const ask = fixture.ask as unknown as ContractEntry[]

afterEach(() => {
  vi.restoreAllMocks()
})

describe('fixture 自身', () => {
  it('ask 串流有內容（空 fixture 會讓下面全部變成空斷言）', () => {
    expect(ask.length).toBeGreaterThan(0)
  })

  it('fixture 只有 ask 這一條串流（多出來的串流代表前端沒有對應 parser）', () => {
    expect(Object.keys(fixture).filter((k) => k !== '_readme')).toEqual(['ask'])
  })
})

describe('parseAskEvent 解得出 fixture 宣告的每一種事件', () => {
  for (const entry of ask.filter((e) => e.frontend === 'parsed')) {
    it(`ask: ${entry.event}`, () => {
      const parsed = parseAskEvent({ event: entry.event, data: entry.data })
      expect(parsed, `${entry.event} 被靜默丟棄`).not.toBeNull()
      expect(parsed!.event).toBe(entry.event)
    })
  }
})

describe('「刻意忽略」與「忘了宣告」必須分得開', () => {
  // 兩者的回傳值都是 null，唯一可觀測的差別是有沒有走 rejectEvent 的警告。
  for (const entry of ask.filter((e) => e.frontend === 'ignored')) {
    it(`ask: ${entry.event} 回 null 且不警告（明確的 case，不是 default）`, () => {
      const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
      expect(parseAskEvent({ event: entry.event, data: entry.data })).toBeNull()
      expect(warn).not.toHaveBeenCalled()
    })
  }

  it('真正未宣告的事件回 null 並警告', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(parseAskEvent({ event: 'totally_new_event', data: {} })).toBeNull()
    expect(warn).toHaveBeenCalledTimes(1)
  })
})
