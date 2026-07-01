import type { JSX } from 'react'
import { BarChart, DonutChart, LineChart } from '@mantine/charts'
import { Box, Text } from '@mantine/core'
import type { ChartBlock } from '../schemas'

const PALETTE = ['blue.6', 'teal.6', 'orange.6', 'grape.6', 'red.6', 'cyan.6']

/**
 * 研報圖表元件。對齊 app/services/chart.py 語意：
 * - pie 遇任一負值 → 拒繪（回傳 null）
 * - series 為空、或 x 為空（不分 bar/line/pie）→ 回傳 null
 */
export function ReportChart({ block }: { block: ChartBlock }): JSX.Element | null {
  if (block.series.length === 0) return null
  if (block.x.length === 0) return null

  if (block.type === 'pie') {
    const values = block.series[0]?.values ?? []
    if (values.some((v) => v < 0)) return null

    const data = block.x.map((xv, i) => ({
      name: String(xv),
      value: values[i] ?? 0,
      color: PALETTE[i % PALETTE.length],
    }))

    return (
      <Box data-testid="report-chart" data-charttype="pie" mb="md">
        <ChartAnnotations title={block.title} unit={block.unit} source={block.source} />
        <DonutChart data={data} size={200} withLabelsLine withLabels />
      </Box>
    )
  }

  const data = block.x.map((xv, i) => {
    const row: Record<string, string | number> = { x: String(xv) }
    block.series.forEach((s) => {
      row[s.name] = s.values[i] ?? 0
    })
    return row
  })
  const series = block.series.map((s, idx) => ({
    name: s.name,
    color: PALETTE[idx % PALETTE.length],
  }))

  if (block.type === 'bar') {
    return (
      <Box data-testid="report-chart" data-charttype="bar" mb="md">
        <ChartAnnotations title={block.title} unit={block.unit} source={block.source} />
        <BarChart h={240} data={data} dataKey="x" series={series} />
      </Box>
    )
  }

  return (
    <Box data-testid="report-chart" data-charttype="line" mb="md">
      <ChartAnnotations title={block.title} unit={block.unit} source={block.source} />
      <LineChart h={240} data={data} dataKey="x" series={series} />
    </Box>
  )
}

function ChartAnnotations({
  title,
  unit,
  source,
}: {
  title?: string | null
  unit?: string | null
  source?: string | null
}): JSX.Element | null {
  if (!title && !unit && !source) return null

  return (
    <>
      {title ? (
        <Text fw={700} size="sm" mb={4}>
          {title}
        </Text>
      ) : null}
      {unit || source ? (
        <Text size="xs" c="dimmed" mb={4}>
          {[unit, source].filter(Boolean).join(' · ')}
        </Text>
      ) : null}
    </>
  )
}
