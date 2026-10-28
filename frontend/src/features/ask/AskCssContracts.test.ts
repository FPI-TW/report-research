import { expect, test } from 'vitest'
import emptyCss from './AskEmptyState.module.css?raw'
import pageCss from './AskPage.module.css?raw'
import drawerCss from './SourcesDrawer.module.css?raw'
import citeCss from './CiteChip.module.css?raw'
import drawerSource from './SourcesDrawer.tsx?raw'
import pageSource from './AskPage.tsx?raw'

/*
 * 研究助理的版面契約：交接包「實測抓到的坑」在這一頁踩得到的幾條。
 *
 * jsdom 不做 layout，這裡驗的是「CSS／原始碼有沒有寫出那個結構」，不是瀏覽器真的排成那樣；
 * 視覺上對不對要開瀏覽器看。抓的是「有人順手改回會出事的寫法」這種結構性回歸。
 */

function stripComments(s: string): string {
  return s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
}

/** 單一類名那條規則的宣告串（不含巢狀，夠用）。 */
function ruleOf(css: string, cls: string): string {
  const m = new RegExp(`(?:^|\\n)\\s*\\.${cls}\\s*\\{([^}]*)\\}`).exec(stripComments(css))
  if (!m) throw new Error(`找不到 .${cls}`)
  return m[1]
}

function keyframesOf(css: string, name: string): string {
  const m = new RegExp(`@keyframes ${name}\\s*\\{([\\s\\S]*?\\})\\s*\\}`).exec(stripComments(css))
  if (!m) throw new Error(`找不到 @keyframes ${name}`)
  return m[1]
}

test('坑 3：空白頁的置中容器（高度 100% 的捲動區內容）只淡入、不位移', () => {
  const anim = /animation:\s*([\w-]+)/.exec(ruleOf(emptyCss, 'wrap'))
  expect(anim).toBeTruthy()
  const frames = keyframesOf(emptyCss, anim![1])
  expect(frames).toMatch(/opacity/)
  expect(frames).not.toMatch(/transform/)
})

test('坑 1：最外層容器 overflow: clip（程式捲不動它），抽屜捲動不用 scrollIntoView', () => {
  expect(ruleOf(pageCss, 'page')).toMatch(/overflow:\s*clip/)
  expect(stripComments(drawerSource)).not.toMatch(/scrollIntoView/)
  expect(stripComments(pageSource)).not.toMatch(/scrollIntoView/)
})

test('坑 2：置中的浮動元素不用 translateX(-50%)（進場 transform 會蓋掉它）', () => {
  for (const css of [pageCss, drawerCss, citeCss]) {
    expect(stripComments(css)).not.toMatch(/-50%/)
  }
  expect(ruleOf(pageCss, 'toBottom')).toMatch(/margin:\s*0 auto/)
})

test('抽屜關閉時延後才切 visibility，滑出動畫才看得到', () => {
  expect(ruleOf(drawerCss, 'panel')).toMatch(/visibility 0s linear 0\.5s/)
})
