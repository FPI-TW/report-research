import type { CSSProperties } from 'react'
import atoms from './atoms.module.css'

/** 骨架塊（交接包「載入、空白、錯誤狀態」：#ececef 圓角塊＋白色掃光）。恆 aria-hidden，忙碌狀態由外層宣告。 */
export function Skel({ w, h, style }: { w: number | string; h: number; style?: CSSProperties }) {
  return <span aria-hidden="true" className={atoms.skel} style={{ width: w, height: h, ...style }} />
}
