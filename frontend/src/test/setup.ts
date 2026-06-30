import '@testing-library/jest-dom/vitest'

// jsdom 未實作 ResizeObserver；Mantine Select / ScrollArea 依賴它。
// GroupBySelect.test.tsx 有個別 beforeAll 版本；此處補全域以覆蓋 SearchPage.test.tsx 等整合測試。
globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

// jsdom 未實作 matchMedia；Mantine MantineProvider 需要它做 color-scheme 偵測。
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
})
