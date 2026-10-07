import { useSyncExternalStore } from 'react'
import type { NoticeKind } from './askSchemas'
import { useFeature } from './useFeatures'

// M11：問答的「搜尋網路」開關。比照 useLocale 用 module 級 store ＋
// useSyncExternalStore：切換在 Composer（可能同時存在中央與底部兩個實例）、消費在
// AskPage 深處的 useAskController，裸 localStorage hook 會讓每個掛載點各持一份副本，
// 症狀是「按了亮起來、送出去的請求卻沒開」。
//
// **預設 false**：維持既有行為與延遲（網搜會讓單題多花數十秒，而 /api/ask 併發只有
// 3 個名額），要開是使用者的明確選擇。localStorage backed，fail-open 到 false——
// 讀不到偏好時寧可少一次外網呼叫，也不要幫使用者做這個決定。
const KEY = 'tf.webSearch'

/**
 * 網搜是否暫停中＝功能旗標 `ask.web_search` 對目前使用者不是開的（`GET /api/features`，Admin v2）。
 *
 * 後端的實際值是伺服器總閘 `ASK_ENABLE_WEB`（環境變數上限）AND 管理員在功能開關頁的覆寫，前後端同一個來源：
 * 先前這裡是寫死的常數（DeepSeek 遷移 PR-W：網搜仍解析到 claude CLI，而 CLI 已於 2026-09-23 放棄），正式環境
 * 以 `ASK_ENABLE_WEB=0` 關閉，所以旗標是關的、行為與寫死時相同。還沒抓到 `/api/features` 或抓失敗也當成暫停。
 * 暫停期間：
 * - 問答輸入框不列網搜開關（`ComposerTools` 的 `useTools()`）。
 * - 請求一律送 `web=false`（`useAskController`），免責文案也不提網路資訊（`Composer`）。
 * - 時效婉拒不顯示「可開網搜」那一句（`noticeDisplayText`，`AssistantMessage` 用）。
 *   localStorage 裡殘留的 `web=true` 不刪、也不送：送了會被總閘擋掉，但 `qa_log.filters.web`
 *   會記到一個使用者看不到的開關狀態。偏好留著，恢復後使用者原本的選擇照舊生效。
 *
 * **接回點**：DeepSeek 版網搜（計畫 P9，Tavily 工具迴圈）完成後，環境檔移除 `ASK_ENABLE_WEB=0`（上限打開），
 * 需要的話再用功能開關頁限定給部分使用者試用；前端不必改。
 */
export function useWebSearchPaused(): boolean {
  return !useFeature('ask.web_search')
}

/**
 * 時效婉拒附的「可開網搜」那一句，與 `app/services/answer.py` 的 `TIME_SENSITIVE_WEB_HINT`／
 * `TIME_SENSITIVE_WEB_HINT_EN` 逐字相同（`tests/test_ask_web_search.py` 讀這支檔案釘住）。
 *
 * 後端附不附看的是「總閘 `ASK_ENABLE_WEB` AND 旗標 `ask.web_search`」（`app/services/answer.py`），與前端的暫停
 * 同源；但歷史紀錄是當時的文字，前端也可能還沒抓到 `/api/features`：照印等於叫使用者去按一顆畫面上沒有的開關。
 * 所以暫停期間在**顯示**時剝掉這一句；落庫的文字不動，它是歷史重播的比對鍵（`NOTICE_MESSAGES`）。
 */
export const TIME_SENSITIVE_WEB_HINTS: readonly string[] = [
  '若需要即時數字，可在輸入框的「＋」開啟「網路搜尋」後再問一次；那類回答來自外部網頁，不是研報內容。',
  ' If you need live figures, turn on “Web search” from the + menu next to the input and ask again; those answers come from external web pages, not from the research reports.',
]

export function noticeDisplayText(text: string, kind: NoticeKind | null, paused: boolean): string {
  if (!paused || kind !== 'time_sensitive') return text
  const hint = TIME_SENSITIVE_WEB_HINTS.find(h => text.endsWith(h))
  return hint ? text.slice(0, -hint.length) : text
}
const listeners = new Set<() => void>()

function read(): boolean {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

let current: boolean = read()

function subscribe(cb: () => void): () => void {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

function getSnapshot(): boolean {
  return current
}

export function setWebSearch(next: boolean): void {
  if (next === current) return
  current = next
  try {
    localStorage.setItem(KEY, next ? '1' : '0')
  } catch {
    /* ignore */
  }
  listeners.forEach((l) => l())
}

export function useWebSearch(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, () => false)
}
