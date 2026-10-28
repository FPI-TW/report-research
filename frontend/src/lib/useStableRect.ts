import { useState } from 'react'
import type { ItemRect, UseFluidHoverReturn } from '@/hooks/use-fluid-hover'

function keyOf(r: ItemRect | null): string {
  return r ? `${r.top},${r.left},${r.width},${r.height}` : 'none'
}

/**
 * 「選取」那塊滑動底色要的位置：第 index 列量好的 rect，重新量測期間（isMeasured 暫時為 false）
 * 沿用上一次的值。
 *
 * 直接拿 `isMeasured ? itemRects[i] : null` 餵 FluidHoverHighlight 會在每次重新量測時把 rect
 * 變成 null 又變回來：高亮被移除、又以同一個 key 立刻回來，AnimatePresence 會把它卡在淡出的
 * 起點（opacity 趨近 0），選取底色就消失了。
 */
export function useStableRect(hover: Pick<UseFluidHoverReturn, 'isMeasured' | 'itemRects'>, index: number): ItemRect | null {
  const measured = index >= 0 && hover.isMeasured ? hover.itemRects[index] ?? null : undefined
  const [state, setState] = useState<{ key: string; rect: ItemRect | null }>({ key: 'init', rect: null })
  if (measured !== undefined) {
    const key = keyOf(measured)
    // 依 React 的「由 props 推導 state」寫法在 render 中更新；值相同就不動，不會無限重繪。
    if (key !== state.key) setState({ key, rect: measured })
  }
  return index >= 0 ? (measured !== undefined ? measured : state.rect) : null
}
