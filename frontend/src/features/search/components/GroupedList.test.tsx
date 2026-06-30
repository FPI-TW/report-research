import React from 'react'
import { render } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { vi } from 'vitest'
import { GroupedList } from './GroupedList'
import type { Row } from '../lib/normalize'
import * as grouping from '../lib/grouping'

function wrap(ui: React.ReactElement) {
  return render(<MantineProvider>{ui}</MantineProvider>)
}

const rows: Row[] = [
  {
    report_id: '1',
    file_name: 'f1',
    market: 'TW',
    report_date: '2026-06-01',
    source: null,
    summary: null,
    report_type: null,
    instrument_types: [],
    relates_stock: false,
    stock_targets: null,
    relates_futures: false,
    futures_targets: null,
  },
  {
    report_id: '2',
    file_name: 'f2',
    market: 'US',
    report_date: '2026-05-01',
    source: null,
    summary: null,
    report_type: null,
    instrument_types: [],
    relates_stock: false,
    stock_targets: null,
    relates_futures: false,
    futures_targets: null,
  },
]

afterEach(() => {
  vi.restoreAllMocks()
})

test('相同 rows/group rerender 時不應重算 groupRows', () => {
  const spy = vi.spyOn(grouping, 'groupRows')
  const onOpen = vi.fn()
  const { rerender } = wrap(
    <GroupedList rows={rows} group="month" mode="browse" onOpen={onOpen} />,
  )

  expect(spy).toHaveBeenCalledTimes(1)

  rerender(
    <MantineProvider>
      <GroupedList rows={rows} group="month" mode="browse" onOpen={onOpen} />
    </MantineProvider>,
  )

  expect(spy).toHaveBeenCalledTimes(1)
})
