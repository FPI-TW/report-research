import { test, expect, vi, beforeAll } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { GroupBySelect } from './GroupBySelect'

// jsdom 未實作 ResizeObserver；Mantine Select 透過 ScrollArea 依賴它。
beforeAll(() => {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
})

const wrap = (ui: React.ReactElement) => render(<MantineProvider>{ui}</MantineProvider>)

test('顯示目前分組值', () => {
  wrap(<GroupBySelect value="month" onChange={vi.fn()} />)
  expect(screen.getByLabelText('分組依據', { selector: 'input' })).toBeInTheDocument()
})
