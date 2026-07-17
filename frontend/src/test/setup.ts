import '@testing-library/jest-dom/vitest'

// jsdom 未實作 matchMedia：提供最小 polyfill。
// query 感知：預設 reduced-motion 為 true，讓 Motion 的 useReducedMotion() 回 true、
// 各動效 wrapper（Reveal/Modal/Popover/SourcesDrawer/TweenNumber）在測試中走即時終態（確定性）；
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
