import React from 'react'
import { Stack, Group, Text, Box } from '@mantine/core'
import type { StatsResponse } from '../schemas'
import type { Filters } from '../lib/filters'
import { SEARCH_SORTS, BROWSE_SORTS } from '../lib/filters'
import { mLabel, mColor, iLabel, iColor, tLabel, SORT_LABELS } from './meta'

// ── activeFilterCount ─────────────────────────────────────────────────────────
// 計算生效中的非預設篩選維度數（市場/商品類型/個股/期貨/類型）。
// 供手機收摺鈕、清除篩選等共用。
export function activeFilterCount(filters: Filters): number {
  let n = 0
  if (filters.market !== '全部') n++
  if (filters.instrument !== '全部') n++
  if (filters.stock) n++
  if (filters.futures) n++
  if (filters.type !== '全部') n++
  return n
}

// ── ChipButton ────────────────────────────────────────────────────────────────
// 以 <button aria-pressed> 實作 chip；不使用 Mantine <Chip>（後者 role=checkbox）。
// 視覺樣式最終由 Task 9 統一萃取；此處用 inline 最簡樣式。
interface ChipButtonProps {
  /** aria-label 用於 accessible name（可包含 code；文字可另顯示中文 label） */
  ariaLabel: string
  active: boolean
  onClick: () => void
  dotColor?: string
  children: React.ReactNode
}

function ChipButton({ ariaLabel, active, onClick, dotColor, children }: ChipButtonProps) {
  return (
    <button
      type="button"
      aria-label={ariaLabel}
      aria-pressed={active}
      onClick={onClick}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '3px 10px',
        border: `1px solid ${active ? '#ae7415' : '#dee2e6'}`,
        borderRadius: 999,
        background: active ? '#fbf3e3' : '#fff',
        color: active ? '#8a5a0f' : '#495057',
        cursor: 'pointer',
        fontSize: 13,
        lineHeight: 1.5,
      }}
    >
      {dotColor && (
        <span
          aria-hidden="true"
          style={{
            display: 'inline-block',
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: dotColor,
            flexShrink: 0,
          }}
        />
      )}
      {children}
    </button>
  )
}

// ── FilterSidebar ─────────────────────────────────────────────────────────────
export interface FilterSidebarProps {
  stats: StatsResponse
  filters: Filters
  onChange: (f: Filters) => void
}

export function FilterSidebar({ stats, filters, onChange }: FilterSidebarProps) {
  // Sort 選項集合隨是否有查詢詞切換；若當前 sort 不在集合內自動退回預設
  const sorts = filters.q ? SEARCH_SORTS : BROWSE_SORTS
  const effectiveSort = sorts.includes(filters.sort) ? filters.sort : sorts[0]

  // 市場清單：全部 + stats.markets
  const markets = [{ market: '全部', count: stats.total_reports }, ...stats.markets]

  // 商品類型清單：全部 + stats.instrument_types
  const instruments = [{ type: '全部', count: stats.total_reports }, ...stats.instrument_types]

  // 報告類型清單：全部 + stats.report_types
  const types = [{ type: '全部', count: stats.total_reports }, ...stats.report_types]

  return (
    <Stack gap="md">
      {/* ── 市場 chip 群組 ─────────────────────────────────── */}
      <Box>
        <Text size="sm" fw={600} mb={6}>
          市場
        </Text>
        <Group gap={6} wrap="wrap">
          {markets.map(({ market, count }) => {
            const isAll = market === '全部'
            // aria-label 含市場代碼（如 "TW 台股"）讓 getByRole(name=/TW/) 能命中
            const ariaLabel = isAll ? '全部' : `${market} ${mLabel(market)}`
            return (
              <ChipButton
                key={market}
                ariaLabel={ariaLabel}
                active={filters.market === market}
                dotColor={isAll ? undefined : mColor(market)}
                onClick={() => onChange({ ...filters, market })}
              >
                <span>{isAll ? '全部' : mLabel(market)}</span>
                <span style={{ color: '#868e96', fontSize: 11, marginLeft: 2 }}>{count}</span>
              </ChipButton>
            )
          })}
        </Group>
      </Box>

      {/* ── 商品類型 chip 群組（有資料才顯示）──────────────── */}
      {stats.instrument_types.length > 0 && (
        <Box>
          <Text size="sm" fw={600} mb={6}>
            商品類型
          </Text>
          <Group gap={6} wrap="wrap">
            {instruments.map(({ type, count }) => {
              const isAll = type === '全部'
              const ariaLabel = isAll ? '全部' : `${type} ${iLabel(type)}`
              return (
                <ChipButton
                  key={type}
                  ariaLabel={ariaLabel}
                  active={filters.instrument === type}
                  dotColor={isAll ? undefined : iColor(type)}
                  onClick={() => onChange({ ...filters, instrument: type })}
                >
                  <span>{isAll ? '全部' : iLabel(type)}</span>
                  <span style={{ color: '#868e96', fontSize: 11, marginLeft: 2 }}>{count}</span>
                </ChipButton>
              )
            })}
          </Group>
        </Box>
      )}

      {/* ── 個股 / 期貨 subject toggles（獨立 boolean，toggle 語意）── */}
      <Box>
        <Text size="sm" fw={600} mb={6}>
          個股 / 期貨
        </Text>
        <Group gap={6}>
          <ChipButton
            ariaLabel="個股"
            active={filters.stock}
            onClick={() => onChange({ ...filters, stock: !filters.stock })}
          >
            <span>個股</span>
          </ChipButton>
          <ChipButton
            ariaLabel="期貨"
            active={filters.futures}
            onClick={() => onChange({ ...filters, futures: !filters.futures })}
          >
            <span>期貨</span>
          </ChipButton>
        </Group>
      </Box>

      {/* ── 報告類型 chip 群組（有資料才顯示）─────────────── */}
      {stats.report_types.length > 0 && (
        <Box>
          <Text size="sm" fw={600} mb={6}>
            類型
          </Text>
          <Group gap={6} wrap="wrap">
            {types.map(({ type, count }) => {
              const isAll = type === '全部'
              const ariaLabel = isAll ? '全部' : tLabel(type)
              return (
                <ChipButton
                  key={type}
                  ariaLabel={ariaLabel}
                  active={filters.type === type}
                  onClick={() => onChange({ ...filters, type })}
                >
                  <span>{isAll ? '全部' : tLabel(type)}</span>
                  <span style={{ color: '#868e96', fontSize: 11, marginLeft: 2 }}>{count}</span>
                </ChipButton>
              )
            })}
          </Group>
        </Box>
      )}

      {/* ── 排序 chip 群組 ─────────────────────────────────── */}
      <Box>
        <Text size="sm" fw={600} mb={6}>
          排序
        </Text>
        <Group gap={6} wrap="wrap">
          {sorts.map((s) => {
            const label = SORT_LABELS[s] ?? s
            return (
              <ChipButton
                key={s}
                ariaLabel={label}
                active={effectiveSort === s}
                onClick={() => onChange({ ...filters, sort: s })}
              >
                <span>{label}</span>
              </ChipButton>
            )
          })}
        </Group>
      </Box>
    </Stack>
  )
}
