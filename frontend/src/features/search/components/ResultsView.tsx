import type { Row } from '../lib/normalize'
import { groupViewMode } from '../lib/grouping'
import { MarketIndex } from './MarketIndex'
import { DrillView } from './DrillView'
import { GroupedList } from './GroupedList'
import { TableView } from './TableView'

interface ResultsViewProps {
  view: 'group' | 'table'
  rows: Row[]
  group: 'month' | 'market'
  mode: 'browse' | 'search'
  terms: string[]
  total: number
  markets: { market: string; count: number }[]
  onOpen: (id: string) => void
  onPickMarket: (market: string) => void
  /** Current market filter — drives index vs drill sub-view */
  market?: string
}

export function ResultsView({
  view,
  rows,
  group,
  mode,
  terms,
  total,
  markets,
  onOpen,
  onPickMarket,
  market = '全部',
}: ResultsViewProps) {
  if (view === 'table') {
    return <TableView rows={rows} mode={mode} onOpen={onOpen} />
  }

  const gMode = groupViewMode(group, market)

  if (gMode === 'index') {
    return <MarketIndex markets={markets} onPickMarket={onPickMarket} />
  }
  if (gMode === 'drill') {
    return (
      <DrillView rows={rows} market={market} mode={mode} onOpen={onOpen} total={total} />
    )
  }
  // grouped (default — group='month', or group='market' with no market selected)
  return <GroupedList rows={rows} group={group} mode={mode} onOpen={onOpen} terms={terms} />
}
