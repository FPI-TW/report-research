import { render } from '@testing-library/react'
import { beforeEach, expect, test, vi } from 'vitest'
import type { Turn } from '../../lib/askReducer'
import { TurnRow, type TurnActions } from './TurnRow'

// 以 render 次數當量尺：AssistantMessage 每 render 一次就把整份答案重新解析成 markdown，
// 那正是 TurnRow 的 memo 要擋掉的成本。
const renders = vi.hoisted(() => ({ byTurn: {} as Record<string, number> }))
vi.mock('./AssistantMessage', () => ({
  AssistantMessage: ({ turn }: { turn: Turn }) => {
    renders.byTurn[turn.id] = (renders.byTurn[turn.id] ?? 0) + 1
    return <div>{turn.answer}</div>
  },
}))

beforeEach(() => { renders.byTurn = {} })

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
  openSources: vi.fn(), toggleSources: vi.fn(),
}

function Flow({ turns }: { turns: Turn[] }) {
  return <>{turns.map(t => <TurnRow key={t.id} turn={t} busy actions={actions} />)}</>
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
    return <TurnRow turn={a} busy={busy} actions={actions} />
  }
  const { rerender } = render(<Probe busy />)
  rerender(<Probe busy={false} />)
  expect(renders.byTurn.a).toBe(2)
})
