import '@testing-library/jest-dom/vitest'

// jsdom 未實作 matchMedia：提供最小 polyfill。
// query 感知：預設 reduced-motion 為 true，讓全站 useTween/.tf-reveal 在測試中確定性跳值；
// 其餘查詢（如寬度斷點）維持 false。需驗動畫路徑的 hook 測試各自 vi.stubGlobal 切換。
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
