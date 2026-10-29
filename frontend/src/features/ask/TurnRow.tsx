import { memo } from 'react'
import type { AnswerView, Turn } from '../../lib/askReducer'
import { AssistantMessage } from './AssistantMessage'
import { UserMessage } from './UserMessage'
import styles from './TurnRow.module.css'

/**
 * 一輪問答要用到的動作。**每一個都必須是穩定參考**（useCallback／setState），
 * 否則下面的 memo 形同虛設——`useAskController` 回傳的物件本身每次 render 都是新的，
 * 所以這裡收的是它裡面的個別函式，不是整個 controller。
 */
export interface TurnActions {
  editResubmit: (turnId: string, qaId: string | null, question: string) => void
  regenerate: (turnId: string, qaId: string | null, question: string) => void
  setFeedback: (turnId: string, qaId: string, value: 'like' | 'dislike' | null) => void
  setVersion: (turnId: string, index: number) => void
  loadVersions: (turnId: string, rootId: string) => Promise<unknown>
  submit: (question: string) => void
  setDraft: (text: string) => void
  /** 開來源抽屜；n 給了就捲到那一則並閃框，null＝只開抽屜。 */
  openSources: (view: AnswerView, n: number | null) => void
  /** 重試、重新生成最後一輪之類「答案會長在最底下」的動作：讓對話流重新貼底。 */
  stickToBottom: () => void
}

interface Props {
  turn: Turn
  busy: boolean
  isLast: boolean
  actions: TurnActions
}

/**
 * 失敗後的「重試」：重問同一題，**不新增版本、也不多一輪**（交接包「失敗」）。
 *
 * - 重新生成失敗（已有舊版本）：再重新生成一次。一個字都沒串出就失敗時 reducer 已把舊版還原成
 *   live，所以這次重生不會把失敗的那次留成一個版本。
 * - 一般提問失敗、而且是最後一輪：原地重送（editResubmit）。失敗的那輪通常已補存成停止列
 *   （有 qaId，見 useAskController 的 keepUnfinished；後端自己記下的失敗列也會收斂到同一列），
 *   帶 edit_of 讓後端停用它再重送，紀錄裡才不會留下失敗的那次。沒存到（qaId 為 null）就不帶，
 *   後端看到的是一題普通的提問。
 * - 其他情形（中間輪失敗之後又問了別的）：原地重送會把後面的輪次一起截掉，只好照舊新開一輪。
 */
function retry(t: Turn, isLast: boolean, a: TurnActions) {
  if (t.priorVersions.length > 0 || t.versionCount > 1) {
    a.regenerate(t.id, t.qaId, t.question)
  } else if (isLast) {
    a.editResubmit(t.id, t.qaId, t.question)
  } else {
    a.submit(t.question)
    return
  }
  if (isLast) a.stickToBottom()
}

/**
 * 單一輪（提問＋回答）。
 *
 * 存在的理由是 memo：串流期間每個 token 都讓 `turns` 換一個新陣列，先前 AskPage 直接在
 * map 裡用行內箭頭函式渲染，於是**所有已完成的輪次**跟著每個 token 重 render，而
 * AssistantMessage 每次都會把整份答案重新解析成 markdown——成本是「輪數 × 答案長度」每 token。
 * reducer 只替換正在串流的那一輪（其餘輪次維持同一個物件參考），所以只要 props 穩定，
 * memo 就能讓已完成的輪次完全不動。
 */
export const TurnRow = memo(function TurnRow({ turn: t, busy, isLast, actions: a }: Props) {
  return (
    <section className={styles.turn} aria-label="一輪對話">
      <UserMessage text={t.question} onEdit={q => a.editResubmit(t.id, t.qaId, q)} disabled={busy} />
      <AssistantMessage
        turn={t}
        isLast={isLast}
        onCite={(n, view) => a.openSources(view, n)}
        onOpenSources={view => a.openSources(view, null)}
        onFeedback={v => t.qaId && a.setFeedback(t.id, t.qaId, v)}
        onNoticeRetry={() => a.setDraft(t.question)}
        onErrorRetry={() => retry(t, isLast, a)}
        onRegenerate={() => {
          a.regenerate(t.id, t.qaId, t.question)
          if (isLast) a.stickToBottom()
        }}
        onFollowup={a.submit}
        disabled={busy}
        onSetVersion={i => {
          // priorVersions「不完整」（而非只有「全空」）就先補載：歷史多版本輪
          // 直接重生後，本地只有剛快照的那一版，中間版本是洞——洞的索引會
          // fallback 到 live，pager 顯示的版號與內容對不上。
          if (t.rootQaId && t.versionCount > 1 && t.priorVersions.length < t.versionCount - 1) {
            void a.loadVersions(t.id, t.rootQaId).then(loaded => {
              if (loaded) a.setVersion(t.id, i)
            })
          } else {
            a.setVersion(t.id, i)
          }
        }}
      />
    </section>
  )
})
