import '@testing-library/jest-dom/vitest'

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
