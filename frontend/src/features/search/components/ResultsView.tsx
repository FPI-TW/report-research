import React from 'react'
import type { Row } from '../lib/normalize'
import type { GroupView } from '../lib/grouping'
import { MarketIndex } from './MarketIndex'
import { DrillView } from './DrillView'
import { GroupedList } from './GroupedList'

interface ResultsViewProps {
  view: GroupView
  rows: Row[]
  group: 'month' | 'market'
  mode: 'browse' | 'search'
  onOpen: (id: string) => void
  onPickMarket: (market: string) => void
  /** Only used when view === 'drill' */
  market?: string
}

export function ResultsView({
  view,
  rows,
  group,
  mode,
  onOpen,
  onPickMarket,
  market = '',
}: ResultsViewProps) {
  if (view === 'index') {
    return <MarketIndex rows={rows} onPickMarket={onPickMarket} />
  }
  if (view === 'drill') {
    return <DrillView rows={rows} market={market} mode={mode} onOpen={onOpen} />
  }
  // grouped (default)
  return <GroupedList rows={rows} group={group} mode={mode} onOpen={onOpen} />
}
