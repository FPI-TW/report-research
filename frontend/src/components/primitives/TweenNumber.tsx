import { useEffect, useRef, useState } from 'react'
import { animate, useReducedMotion } from 'motion/react'
import { TF_EASE_OUT } from '../../lib/motionTokens'

interface TweenNumberProps {
  value: number
  /** 格式化函式（如 fmtInt、n => n.toFixed(1)）。省略則直接字串化。 */
  format?: (n: number) => string
  /** 補間時長（ms）。 */
  duration?: number
  /** 小數位數；整數計數傳 0、百分比傳 1。 */
  decimals?: number
}

/**
 * Motion animate() 數字補間，取代舊 useTween 的手刻 rAF。
 * - reduced-motion 或首次顯示 → 直接跳到真值（不從 0 數起）。
 * - 從當前顯示值 retarget，連續更新不跳；target 未變則短路。
 */
export function TweenNumber({ value, format, duration = 340, decimals = 0 }: TweenNumberProps) {
  const reduced = useReducedMotion()
  const [display, setDisplay] = useState(value)
  const fromRef = useRef(value) // 最近顯示值，作為 retarget 起點
  const firstRef = useRef(true)

  useEffect(() => {
    const first = firstRef.current
    firstRef.current = false
    if (reduced || first) {
      fromRef.current = value
      setDisplay(value)
      return
    }
    if (value === fromRef.current) return
    const controls = animate(fromRef.current, value, {
      duration: duration / 1000,
      ease: TF_EASE_OUT,
      onUpdate: v => { fromRef.current = v; setDisplay(v) },
    })
    return () => controls.stop()
  }, [value, reduced, duration])

  const f = 10 ** decimals
  const v = Math.round(display * f) / f
  return <>{format ? format(v) : String(v)}</>
}
