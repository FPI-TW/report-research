import React from 'react'
import type { Row } from '../lib/normalize'
import { mLabel, mColor } from './meta'
import { GroupedList } from './GroupedList'

interface DrillViewProps {
  rows: Row[]
  market: string
  mode: 'browse' | 'search'
  onOpen: (id: string) => void
}

// DrillView always sub-groups by month.
export function DrillView({ rows, market, mode, onOpen }: DrillViewProps) {
  const color = mColor(market)

  return (
    <div data-testid="drill-view">
      <div
        data-testid="drill-header"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '8px 12px',
          background: '#f8f9fa',
          borderBottom: '1px solid #e9ecef',
        }}
      >
        <span
          aria-hidden="true"
          style={{
            display: 'inline-block',
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: color,
            flexShrink: 0,
          }}
        />
        <span style={{ fontWeight: 600 }}>{mLabel(market)}</span>
      </div>
      <GroupedList rows={rows} group="month" mode={mode} onOpen={onOpen} />
    </div>
  )
}
