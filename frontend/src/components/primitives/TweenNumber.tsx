import { useTween } from '../../lib/useTween'

interface TweenNumberProps {
  value: number
  /** 格式化函式（如 fmtInt、n => n.toFixed(1)）。省略則直接字串化。 */
  format?: (n: number) => string
  duration?: number
  /** 小數位數；整數計數傳 0、百分比傳 1。 */
  decimals?: number
}

/** 把 useTween 包成可直接放進 JSX 的數字，reduced-motion 時直接跳值。 */
export function TweenNumber({ value, format, duration, decimals = 0 }: TweenNumberProps) {
  const v = useTween(value, { duration, decimals })
  return <>{format ? format(v) : String(v)}</>
}
