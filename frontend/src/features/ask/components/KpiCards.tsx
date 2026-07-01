import type { JSX } from 'react'
import { Badge, Group, Paper, Stack, Text } from '@mantine/core'
import type { KpiBlock } from '../schemas'

/** KPI 卡列。對齊 app/services/pdf.py inject_kpi 語意：無 items → 不渲染。 */
export function KpiCards({ block }: { block: KpiBlock }): JSX.Element | null {
  if (block.items.length === 0) return null

  return (
    <Group data-testid="report-kpi" gap="sm" wrap="wrap" mb="md">
      {block.items.map((item, i) => {
        const dir = item.dir ?? ''
        const color = dir === 'up' ? 'teal.7' : dir === 'down' ? 'red.7' : 'dimmed'
        return (
          <Paper key={i} data-testid="report-kpi-item" withBorder radius="md" p="sm">
            <Stack gap={2} align="center">
              <Text fw={700} size="xl">
                {item.value}
              </Text>
              <Text size="xs" c="dimmed">
                {item.label}
              </Text>
              {item.change ? (
                <Text data-testid="report-kpi-change" data-dir={dir} fw={700} size="xs" c={color}>
                  {item.change}
                </Text>
              ) : null}
              {item.source ? (
                <Badge data-testid="report-kpi-source" variant="light" color="gray" size="xs">
                  {item.source}
                </Badge>
              ) : null}
            </Stack>
          </Paper>
        )
      })}
    </Group>
  )
}
