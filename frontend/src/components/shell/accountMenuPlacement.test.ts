import { expect, test } from 'vitest'
import { floatingPlacement } from './accountMenuPlacement'

// DOMRect 的最小替身：floatingPlacement 只讀 top／right／bottom。
const rect = (r: Partial<DOMRect>) => ({ top: 0, right: 0, bottom: 0, left: 0, width: 0, height: 0, x: 0, y: 0, ...r }) as DOMRect

test('row（展開側欄）不浮出：沿用 CSS 的容器內定位', () => {
  expect(floatingPlacement('row', rect({ right: 40, bottom: 900 }), 1440, 900)).toBeUndefined()
})

test('mini（收合側欄）：開在側欄右側、底邊對齊頭像、固定寬度', () => {
  // 收合側欄 60px 寬，頭像在左下角（right=47、bottom=886）
  const p = floatingPlacement('mini', rect({ top: 852, right: 47, bottom: 886 }), 1440, 900)
  expect(p).toMatchObject({ position: 'fixed', left: 55, right: 'auto', bottom: 14, width: 248 })
})

test('mobile（手機分頁列）：開在分頁列上方、右緣對齊頭像、不超出視窗', () => {
  const p = floatingPlacement('mobile', rect({ top: 790, right: 370, bottom: 830 }), 390, 844)
  expect(p).toMatchObject({ position: 'fixed', left: 'auto', right: 20, bottom: 62, width: 248 })
})

test('很窄的視窗：寬度縮到視窗減兩側間距，貼著右緣也留 8px', () => {
  const p = floatingPlacement('mobile', rect({ top: 600, right: 300, bottom: 640 }), 240, 700)
  expect(p).toMatchObject({ width: 224, right: 8 })
  const m = floatingPlacement('mini', rect({ top: 600, right: 230, bottom: 640 }), 240, 700)
  // 右側放不下時往左收，左緣至少留 8px，整個選單仍在視窗內
  expect(m).toMatchObject({ width: 224, left: 8 })
})
