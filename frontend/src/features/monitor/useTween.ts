import { useEffect, useRef, useState } from 'react'

/** 600ms 三次方緩動的數字動畫；尊重 reduced-motion（直接回終值）。 */
export function useTween(value: number | null): number | null {
  const [shown, setShown] = useState<number | null>(value)
  const from = useRef<number | null>(value)

  useEffect(() => {
    const reduce =
      typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches

    if (value == null) {
      from.current = null
      // 透過 RAF 回呼更新 state（符合 react-hooks/set-state-in-effect 規則）
      const raf = requestAnimationFrame(() => setShown(null))
      return () => cancelAnimationFrame(raf)
    }

    const start = from.current ?? value

    if (reduce || start === value) {
      from.current = value
      // 同上：透過 RAF 回呼更新 state
      const raf = requestAnimationFrame(() => setShown(value))
      return () => cancelAnimationFrame(raf)
    }

    const t0 = performance.now()
    let raf = 0
    const step = (t: number) => {
      const p = Math.min(1, (t - t0) / 600)
      const e = 1 - Math.pow(1 - p, 3)
      setShown(start + (value - start) * e)
      if (p < 1) raf = requestAnimationFrame(step)
      else from.current = value
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [value])

  return shown
}
