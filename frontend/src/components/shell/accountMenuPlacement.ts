import type { CSSProperties } from 'react'

export type AccountMenuVariant = 'mini' | 'row' | 'mobile'

/** 浮出式選單（mini／mobile）的寬度與離觸發點、視窗邊緣的間距。 */
const FLOAT_WIDTH = 248
const FLOAT_GAP = 8

/**
 * mini／mobile 的選單座標（視窗座標，搭配 Popover portal）。
 *
 * 為什麼不能沿用 row 的 `left/right: 10px`：那會把寬度綁在外層容器上——收合側欄只有 60px、
 * 手機分頁列的帳號格也只有一格寬，選單被壓成一個字一行。而且這兩個容器都是
 * `overflow: hidden`＋`backdrop-filter`，在容器內再寬的選單也會被裁掉，所以改成 portal 到
 * body、以觸發點的實際位置定位：收合側欄開在側欄右側、底邊對齊頭像；手機開在分頁列上方、
 * 右緣對齊頭像。row（展開側欄）維持原本的容器內定位。
 */
export function floatingPlacement(variant: AccountMenuVariant, anchor: DOMRect, vw: number, vh: number): CSSProperties | undefined {
  if (variant === 'row') return undefined
  const width = Math.max(0, Math.min(FLOAT_WIDTH, vw - FLOAT_GAP * 2))
  if (variant === 'mini') {
    const left = Math.min(anchor.right + FLOAT_GAP, Math.max(FLOAT_GAP, vw - width - FLOAT_GAP))
    return { position: 'fixed', left, right: 'auto', bottom: Math.max(FLOAT_GAP, vh - anchor.bottom), width }
  }
  return {
    position: 'fixed', left: 'auto', right: Math.max(FLOAT_GAP, vw - anchor.right),
    bottom: Math.max(FLOAT_GAP, vh - anchor.top + FLOAT_GAP), width,
  }
}
