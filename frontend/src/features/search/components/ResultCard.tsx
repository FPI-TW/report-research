import React from 'react'
import type { Row } from '../lib/normalize'
import { mLabel, mColor, iLabel, fmtDate, tLabel } from './meta'

interface ResultCardProps {
  row: Row
  mode: 'browse' | 'search'
  onOpen: (id: string) => void
}

export function ResultCard({ row, mode, onOpen }: ResultCardProps) {
  const color = mColor(row.market ?? '')

  // Info row: source · date · type · (search: matchCount)
  const infoParts: string[] = []
  if (row.source) infoParts.push(row.source)
  const dateStr = fmtDate(row.report_date)
  if (dateStr) infoParts.push(dateStr)
  if (row.report_type) infoParts.push(tLabel(row.report_type))

  const passage = (row.passages ?? [])[0]
  const firstPassage = passage ? passage.content.slice(0, 300) : null

  const scorePct = mode === 'search'
    ? Math.max(4, Math.min(100, Math.round((row.bestScore ?? 0) * 100)))
    : 0

  const handleClick = () => onOpen(row.report_id)
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onOpen(row.report_id)
    }
  }

  const cardContent = (
    <>
      {/* Top row: market badge + filename + rank */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span
          data-testid="market-badge"
          style={{
            background: color,
            color: '#fff',
            borderRadius: 4,
            padding: '1px 7px',
            fontSize: 12,
            fontWeight: 600,
            flexShrink: 0,
          }}
        >
          {mLabel(row.market ?? '')}
        </span>
        <span style={{ fontWeight: 500, flex: 1 }} title={row.file_name}>
          {row.file_name}
        </span>
        {mode === 'search' && row.rank != null && (
          <span style={{ fontSize: 12, color: '#868e96' }}>#{row.rank}</span>
        )}
      </div>

      {/* Info row */}
      {infoParts.length > 0 && (
        <div style={{ fontSize: 12, color: '#868e96', marginTop: 4 }}>
          {infoParts.map((part, i) => (
            <React.Fragment key={i}>
              {i > 0 && <span style={{ margin: '0 4px' }}>·</span>}
              <span>{part}</span>
            </React.Fragment>
          ))}
          {mode === 'search' && row.matchCount != null && (
            <>
              <span style={{ margin: '0 4px' }}>·</span>
              <span data-testid="match-count">命中 {row.matchCount} 片段</span>
            </>
          )}
        </div>
      )}
      {/* matchCount-only row when infoParts empty (edge case) */}
      {infoParts.length === 0 && mode === 'search' && row.matchCount != null && (
        <div style={{ fontSize: 12, color: '#868e96', marginTop: 4 }}>
          <span data-testid="match-count">命中 {row.matchCount} 片段</span>
        </div>
      )}

      {/* Instrument type tags */}
      {(row.instrument_types ?? []).length > 0 && (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 4 }}>
          {(row.instrument_types ?? []).map((t) => (
            <span
              key={t}
              style={{
                border: '1px solid #dee2e6',
                borderRadius: 4,
                padding: '1px 6px',
                fontSize: 11,
              }}
            >
              {iLabel(t)}
            </span>
          ))}
        </div>
      )}

      {/* Summary */}
      {row.summary && (
        <div style={{ fontSize: 13, color: '#495057', marginTop: 6 }}>{row.summary}</div>
      )}

      {/* Search-mode extras: relevance bar + first passage */}
      {mode === 'search' && (
        <>
          <div
            data-testid="score-bar-track"
            style={{
              marginTop: 6,
              background: '#e9ecef',
              borderRadius: 2,
              height: 4,
              overflow: 'hidden',
            }}
          >
            <div
              data-testid="score-bar-fill"
              style={{ width: `${scorePct}%`, background: '#228be6', height: '100%' }}
            />
          </div>
          {firstPassage && (
            <div
              data-testid="passage-text"
              style={{
                fontSize: 12,
                color: '#495057',
                marginTop: 6,
                background: '#f8f9fa',
                padding: '4px 8px',
                borderRadius: 4,
              }}
            >
              {firstPassage}
            </div>
          )}
        </>
      )}
    </>
  )

  if (mode === 'browse') {
    return (
      <div
        data-testid="result-card"
        data-report-id={row.report_id}
        role="button"
        tabIndex={0}
        onClick={handleClick}
        onKeyDown={handleKeyDown}
        style={{ cursor: 'pointer', padding: '10px 12px', borderBottom: '1px solid #f1f3f5' }}
      >
        {cardContent}
      </div>
    )
  }

  // search mode — not whole-card clickable
  return (
    <div
      data-testid="result-card"
      data-report-id={row.report_id}
      style={{ padding: '10px 12px', borderBottom: '1px solid #f1f3f5' }}
    >
      {cardContent}
    </div>
  )
}
