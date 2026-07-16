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
 * - reduced-motion → 直接跳到真值（不動畫，維持測試 determinism）。
 * - 允許動畫時，首次進場從 0 數起（count-up 效果）；之後從當前顯示值 retarget，
 *   連續更新不跳、target 未變則短路。
 */
export function TweenNumber({ value, format, duration = 340, decimals = 0 }: TweenNumberProps) {
  const reduced = useReducedMotion()
  // 非 reduced 時首幀從 0 起跳（count-up）；reduced 於 render 直接取真值，不進 effect setState
  const [display, setDisplay] = useState(0)
  const fromRef = useRef(0) // 最近顯示值，作為 retarget 起點
  const firstRef = useRef(true)

  useEffect(() => {
    const first = firstRef.current
    firstRef.current = false
    if (reduced) { fromRef.current = value; return }
    if (!first && value === fromRef.current) return
    const controls = animate(fromRef.current, value, {
      // 進場 count-up 給足時長；後續 retarget 用原本節奏
      duration: (first ? Math.max(duration, 700) : duration) / 1000,
      ease: TF_EASE_OUT,
      onUpdate: v => { fromRef.current = v; setDisplay(v) },
    })
    return () => controls.stop()
  }, [value, reduced, duration])

  const f = 10 ** decimals
  // reduced 直接呈現真值，避開 effect 內 setState 與動畫
  const v = Math.round((reduced ? value : display) * f) / f
  return <>{format ? format(v) : String(v)}</>
}
