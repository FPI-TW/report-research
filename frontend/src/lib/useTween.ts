import { useEffect, useRef, useState } from 'react'
import { usePrefersReducedMotion } from './usePrefersReducedMotion'

interface UseTweenOptions {
  /** 補間時長（ms）。預設 340（--tf-dur-4）。 */
  duration?: number
  /** 回傳前四捨五入到 N 位小數；省略＝回傳原始浮點（消費端自行格式化）。 */
  decimals?: number
  /** 首次顯示是否從舊值補間；預設 false＝直接顯示真值，不從 0 數起。 */
  animateInitial?: boolean
}

const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3)

/**
 * rAF 數字補間。
 * - reduced-motion 或無 rAF（jsdom/SSR）→ 立即跳到 target（無障礙契約＋測試確定性）。
 * - 從當前顯示值 retarget，連續更新不跳；target 未變則短路（防輪詢/每秒時鐘 re-render 重啟）。
 * - 用 rAF timestamp（非 Date.now）→ fake-timers 友善。
 */
export function useTween(target: number, opts: UseTweenOptions = {}): number {
  const { duration = 340, decimals, animateInitial = false } = opts
  const reduced = usePrefersReducedMotion()
  const [display, setDisplay] = useState(target)
  const rafRef = useRef(0)
  const fromRef = useRef(target)
  const startRef = useRef(0)
  const lastTargetRef = useRef(target)
  const mountedRef = useRef(false)
  const valueRef = useRef(target) // 最近實際顯示值；僅於 effect / rAF 回呼內寫入（不在 render 期改 ref）

  useEffect(() => {
    const first = !mountedRef.current
    mountedRef.current = true
    const canRaf = typeof requestAnimationFrame === 'function'

    if (reduced || !canRaf || (first && !animateInitial)) {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      lastTargetRef.current = target
      valueRef.current = target
      setDisplay(target)
      return
    }
    if (target === lastTargetRef.current) return

    fromRef.current = valueRef.current
    lastTargetRef.current = target
    startRef.current = 0
    const tick = (ts: number) => {
      if (!startRef.current) startRef.current = ts
      const t = Math.min(1, (ts - startRef.current) / duration)
      const v = t >= 1 ? target : fromRef.current + (target - fromRef.current) * easeOutCubic(t)
      valueRef.current = v
      setDisplay(v)
      if (t < 1) rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current) }
  }, [target, reduced, duration, animateInitial])

  if (decimals == null) return display
  const f = 10 ** decimals
  return Math.round(display * f) / f
}
