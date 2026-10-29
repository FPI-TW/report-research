import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, expect, test, vi } from 'vitest'
import type { Turn } from '../../lib/askReducer'
import { TurnRow, type TurnActions } from './TurnRow'

// 以 render 次數當量尺：AssistantMessage 每 render 一次就把整份答案重新解析成 markdown，
// 那正是 TurnRow 的 memo 要擋掉的成本。mock 版另外露出「重試」，讓重試的分流可以直接測。
const renders = vi.hoisted(() => ({ byTurn: {} as Record<string, number> }))
vi.mock('./AssistantMessage', () => ({
  AssistantMessage: ({ turn, onErrorRetry }: { turn: Turn; onErrorRetry: () => void }) => {
    renders.byTurn[turn.id] = (renders.byTurn[turn.id] ?? 0) + 1
    return <div>{turn.answer}<button type="button" onClick={onErrorRetry}>重試</button></div>
  },
}))

beforeEach(() => { renders.byTurn = {}; vi.clearAllMocks() })

function makeTurn(over: Partial<Turn>): Turn {
  return {
    id: 't', question: 'Q', phase: 'done', stages: [], webUsed: false, retrievedCount: null,
    answer: '答案', thinkingMs: 3000, startedAt: 0, sources: [], extSources: [], qaId: 'qa1',
    isOfftopic: false, noticeText: null, noticeKind: null, feedback: null, errorText: null,
    followups: [], priorVersions: [], versionIndex: 0, rootQaId: null, versionCount: 1, queuePosition: null,
    ...over,
  }
}

const actions: TurnActions = {
  editResubmit: vi.fn(), regenerate: vi.fn(), setFeedback: vi.fn(), setVersion: vi.fn(),
  loadVersions: vi.fn(async () => true), submit: vi.fn(), setDraft: vi.fn(),
  openSources: vi.fn(), stickToBottom: vi.fn(),
}

function Flow({ turns }: { turns: Turn[] }) {
  return <>{turns.map((t, i) => <TurnRow key={t.id} turn={t} busy isLast={i === turns.length - 1} actions={actions} />)}</>
}

test('串流中逐 token 更新：只有正在串流的那一輪重 render，已完成的輪次不動', () => {
  const done = makeTurn({ id: 'done' })
  const live = makeTurn({ id: 'live', phase: 'streaming', answer: '' })
  const { rerender } = render(<Flow turns={[done, live]} />)
  expect(renders.byTurn).toEqual({ done: 1, live: 1 })

  // 與 askReducer 同樣的更新方式：陣列換新、只替換串流中的那一輪，其餘維持原物件參考。
  for (const answer of ['台', '台積', '台積電']) {
    rerender(<Flow turns={[done, { ...live, answer }]} />)
  }
  expect(renders.byTurn).toEqual({ done: 1, live: 4 })
})

test('busy 切換（串流結束）時每一輪都要更新，按鈕才會解鎖', () => {
  const a = makeTurn({ id: 'a' })
  function Probe({ busy }: { busy: boolean }) {
    return <TurnRow turn={a} busy={busy} isLast actions={actions} />
  }
  const { rerender } = render(<Probe busy />)
  rerender(<Probe busy={false} />)
  expect(renders.byTurn.a).toBe(2)
})

// ── 失敗後的重試：重問同一題，不新增版本、也不多一輪 ─────────────────────────

test('最後一輪的一般提問失敗：原地重送（不帶 edit_of、不多開一輪），並貼回底部', () => {
  const t = makeTurn({ id: 'e', phase: 'error', qaId: null, answer: '' })
  render(<TurnRow turn={t} busy={false} isLast actions={actions} />)
  fireEvent.click(screen.getByRole('button', { name: '重試' }))
  expect(actions.editResubmit).toHaveBeenCalledWith('e', null, 'Q')
  expect(actions.submit).not.toHaveBeenCalled()
  expect(actions.regenerate).not.toHaveBeenCalled()
  expect(actions.stickToBottom).toHaveBeenCalled()
})

test('最後一輪的一般提問失敗、已補存成停止列：原地重送並帶 edit_of 停用那一列', () => {
  const t = makeTurn({ id: 'e', phase: 'error', qaId: 'qa-stop', answer: '' })
  render(<TurnRow turn={t} busy={false} isLast actions={actions} />)
  fireEvent.click(screen.getByRole('button', { name: '重試' }))
  expect(actions.editResubmit).toHaveBeenCalledWith('e', 'qa-stop', 'Q')
  expect(actions.submit).not.toHaveBeenCalled()
  expect(actions.regenerate).not.toHaveBeenCalled()
})

test('重新生成失敗：再重新生成一次（沿用版本鏈），不另開一輪', () => {
  const prior = { answer: '舊答案', sources: [], extSources: [], qaId: 'qa0', thinkingMs: null, stages: [], feedback: null, followups: [], stopped: false }
  const t = makeTurn({ id: 'r', phase: 'error', qaId: 'qa0', priorVersions: [prior], versionCount: 2, versionIndex: 1 })
  render(<TurnRow turn={t} busy={false} isLast actions={actions} />)
  fireEvent.click(screen.getByRole('button', { name: '重試' }))
  expect(actions.regenerate).toHaveBeenCalledWith('r', 'qa0', 'Q')
  expect(actions.editResubmit).not.toHaveBeenCalled()
  expect(actions.submit).not.toHaveBeenCalled()
})

test('中間輪失敗：原地重送會截掉後面的輪次，只好照舊新開一輪', () => {
  const t = makeTurn({ id: 'm', phase: 'error', qaId: null, answer: '' })
  render(<TurnRow turn={t} busy={false} isLast={false} actions={actions} />)
  fireEvent.click(screen.getByRole('button', { name: '重試' }))
  expect(actions.submit).toHaveBeenCalledWith('Q')
  expect(actions.editResubmit).not.toHaveBeenCalled()
})
