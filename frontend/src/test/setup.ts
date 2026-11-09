import '@testing-library/jest-dom/vitest'

// jsdom 未實作 matchMedia：提供最小 polyfill。
// query 感知：預設 reduced-motion 為 true，讓 Motion 的 useReducedMotion() 回 true、
// 各動效 wrapper（Reveal/Modal/Popover/SourcesDrawer）在測試中走即時終態（確定性）；
// 其餘查詢（如寬度斷點）維持 false。
if (!window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: /prefers-reduced-motion/.test(query),
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList
}

// Radix（下拉選單、提示框的定位）與 Fluid 的滑動高亮會量元素大小，jsdom 沒有 ResizeObserver
// 與指標捕捉 API：給最小的空實作，元件照常掛載與開關（jsdom 本來就不算版面）。
if (!('ResizeObserver' in window)) {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  ;(window as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub
}
if (!Element.prototype.hasPointerCapture) Element.prototype.hasPointerCapture = () => false
if (!Element.prototype.releasePointerCapture) Element.prototype.releasePointerCapture = () => {}
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}

// 介面字典：正式環境依語系 lazy 載入（src/i18n/index.ts），測試一次同步登記全部，預設語系 zh-Hant。
// 每題結束把語系切回 zh-Hant，並清掉偏好（localStorage 與 cookie），切換語系的測試不會污染下一題。
import { afterEach } from 'vitest'
import { registerMessages, setLocale } from '../i18n'
import zhApp from '../i18n/messages/zh-Hant/app'
import zhAdmin from '../i18n/messages/zh-Hant/admin'
import enApp from '../i18n/messages/en/app'
import enAdmin from '../i18n/messages/en/admin'

registerMessages('zh-Hant', 'app', zhApp)
registerMessages('zh-Hant', 'admin', zhAdmin)
registerMessages('en', 'app', enApp)
registerMessages('en', 'admin', enAdmin)

afterEach(() => {
  setLocale('zh-Hant')
  try { localStorage.removeItem('tf.locale') } catch { /* ignore */ }
  document.cookie = 'tf_locale=; path=/; max-age=0'
})
