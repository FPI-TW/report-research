import { useSyncExternalStore } from 'react'

// M10b：輸出語言偏好（zh-Hant / en）。以 module 級 store + useSyncExternalStore 共享——
// 語言切換在側欄 AccountMenu、消費在 AskPage 深處的 useAskController，兩者不同子樹，
// 需單一共享來源（裸 localStorage hook 會各持一份副本）。localStorage backed、fail-open
// 到 zh-Hant，對齊後端 resolve_locale。
export type Locale = 'zh-Hant' | 'en'

const KEY = 'tf.locale'
const listeners = new Set<() => void>()

function read(): Locale {
  try {
    return localStorage.getItem(KEY) === 'en' ? 'en' : 'zh-Hant'
  } catch {
    return 'zh-Hant'
  }
}

let current: Locale = read()

function subscribe(cb: () => void): () => void {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

function getSnapshot(): Locale {
  return current
}

export function setLocale(next: Locale): void {
  if (next === current) return
  current = next
  try {
    localStorage.setItem(KEY, next)
  } catch {
    /* ignore */
  }
  listeners.forEach((l) => l())
}

export function useLocale(): Locale {
  return useSyncExternalStore(subscribe, getSnapshot, () => 'zh-Hant')
}
