import { useEffect, useRef, useState } from 'react'
import { usePrefersReducedMotion } from './usePrefersReducedMotion'

interface UsePresenceOptions {
  /** 離場動畫時長（ms）；卸載延遲以此為準。預設 240（--tf-dur-3）。 */
  duration?: number
}

export interface Presence {
  /** 是否要渲染（含離場動畫期間）。 */
  isMounted: boolean
  /** 供 CSS data-state 用：'open' 播進場態、'closed' 播離場/初始態。 */
  state: 'open' | 'closed'
}

/**
 * 覆蓋層進出場：同步掛載、延遲卸載。
 * - 掛載由 open||exiting 導出，故 useFocusTrap(ref, open) 維持以 open 為鍵、焦點於關閉瞬間即釋放。
 * - 關閉時於「同一次 render」同步設 exiting=true（React「prop 改變即調整 state」模式），isMounted 不會瞬間掉成
 *   false，面板元素身分得以保留 → 離場 CSS 過渡才會播（否則元素被卸載重建，過渡無起始值）。
 * - 離場計時以 exiting 為鍵，reduced/duration 中途變動會重排、不會卡住。
 * - reduced-motion／無 rAF：state 直接導出 'open'（過渡另由 CSS blanket 歸零），不需等 entered。
 */
export function usePresence(open: boolean, opts: UsePresenceOptions = {}): Presence {
  const { duration = 240 } = opts
  const reduced = usePrefersReducedMotion()
  const canRaf = typeof requestAnimationFrame === 'function'
  const [prevOpen, setPrevOpen] = useState(open)
  const [exiting, setExiting] = useState(false)
  const [entered, setEntered] = useState(false)
  const rafRef = useRef(0)

  // render 期同步反應 open 變化（非 effect，故不會有「掉一幀」的卸載重建）
  if (open !== prevOpen) {
    setPrevOpen(open)
    if (open) setExiting(false)
    else if (prevOpen) { setExiting(true); setEntered(false) }
  }

  // 進場：掛載後雙 rAF 翻 entered=true 觸發過渡（僅在會播動畫時；設值都在 rAF 回呼內、非同步 setState）
  useEffect(() => {
    if (!open || !canRaf || reduced) return
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = requestAnimationFrame(() => setEntered(true))
    })
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current) }
  }, [open, canRaf, reduced])

  // 離場計時：只要 exiting 為真就（重新）排定卸載；設值在 setTimeout 回呼內、非同步 setState
  useEffect(() => {
    if (!exiting) return
    const t = setTimeout(() => setExiting(false), reduced ? 0 : duration)
    return () => clearTimeout(t)
  }, [exiting, reduced, duration])

  const state = open && (entered || reduced || !canRaf) ? 'open' : 'closed'
  return { isMounted: open || exiting, state }
}
