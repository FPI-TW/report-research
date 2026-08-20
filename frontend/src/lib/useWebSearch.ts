import { useSyncExternalStore } from 'react'

// M11：問答的「搜尋網路」開關。比照 useLocale 用 module 級 store ＋
// useSyncExternalStore：切換在 Composer（可能同時存在中央與底部兩個實例）、消費在
// AskPage 深處的 useAskController，裸 localStorage hook 會讓每個掛載點各持一份副本，
// 症狀是「按了亮起來、送出去的請求卻沒開」。
//
// **預設 false**：維持既有行為與延遲（網搜會讓單題多花數十秒，而 /api/ask 併發只有
// 3 個名額），要開是使用者的明確選擇。localStorage backed，fail-open 到 false——
// 讀不到偏好時寧可少一次外網呼叫，也不要幫使用者做這個決定。
const KEY = 'tf.webSearch'
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
