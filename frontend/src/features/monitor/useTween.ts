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
      const cur = start + (value - start) * e
      // 每幀同步基準：中途換值/卸載時，下次動畫以最新可見值起跳，避免過時基準（p=1 時 cur===value）
      from.current = cur
      setShown(cur)
      if (p < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  // deps: 僅 value；reduce 由系統媒體狀態決定、非 render 觸發，刻意不列入
  }, [value])

  return shown
}
