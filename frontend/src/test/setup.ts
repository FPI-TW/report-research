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
