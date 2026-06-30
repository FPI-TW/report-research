import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge, Card, Group, Progress, SimpleGrid, Stack, Text, Title } from '@mantine/core'
import { getProgress } from '../../lib/api'
import { ingestRateText, rateText } from '../../lib/eta'
import { useMonitorRate } from './useMonitorRate'
import { useTween } from './useTween'

const nf = (n: number | null | undefined) =>
  n == null ? '—' : Math.round(Number(n)).toLocaleString('en-US')

function Tile({
  label,
  value,
  suffix,
  sub,
}: {
  label: string
  value: number | null
  suffix?: string
  sub?: string
}) {
  const shown = useTween(value)
  return (
    <Card withBorder padding="md" radius="md">
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <Text fw={700} size="xl">
        {nf(shown)}
        {suffix ? (
          <Text span size="sm" c="dimmed">
            {suffix}
          </Text>
        ) : null}
      </Text>
      {sub ? (
        <Text size="xs" c="dimmed">
          {sub}
        </Text>
      ) : null}
    </Card>
  )
}

export default function MonitorPage() {
  // 在地每秒時鐘（平價 §6.3：與舊 monitor.html setInterval(clock,1000) 等效）
  const [now, setNow] = useState(() => new Date().toTimeString().slice(0, 8))
  useEffect(() => {
    const id = setInterval(() => setNow(new Date().toTimeString().slice(0, 8)), 1000)
    return () => clearInterval(id)
  }, [])

  const { data, isError, isPending } = useQuery({
    queryKey: ['progress'],
    queryFn: getProgress,
    refetchInterval: 2000,
    refetchIntervalInBackground: true,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })

  const sample = useMemo(
    () =>
      data
        ? {
            reports: data.db.reports,
            chunks: data.db.chunks,
            sumDone: data.summary?.done ?? null,
            tagDone: data.tagging?.done ?? null,
          }
        : null,
    [data],
  )
  const rate = useMonitorRate(sample)

  const tag = data?.tagging
  const sum = data?.summary
  const ing = data?.ingest
  const orch = data?.orchestrator
  // 首次載入、尚無資料：顯示「連線中」灰標而非 LIVE（避免暗示已有資料）
  const connecting = isPending

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>研報導入監控</Title>
        <Group gap="xs">
          <Text size="sm" c="dimmed" ff="monospace">
            {now}
          </Text>
          <Badge
            color={isError ? 'red' : connecting ? 'gray' : 'gold'}
            variant={isError || connecting ? 'light' : 'filled'}
          >
            {isError ? '重連中' : connecting ? '連線中' : 'LIVE'}
          </Badge>
        </Group>
      </Group>

      <SimpleGrid cols={{ base: 2, sm: 4 }}>
        <Tile
          label="已導入報告"
          value={data?.db.reports ?? null}
          sub={`${data?.db.markets.length ?? 0} 個市場`}
        />
        <Tile label="總片段 CHUNKS" value={data?.db.chunks ?? null} sub="向量片段總數" />
        <Tile
          label="標註進度"
          value={tag ? tag.pct : null}
          suffix="%"
          sub={tag ? `已標註 ${nf(tag.done)} / ${nf(tag.total)}` : ''}
        />
        <Tile
          label="摘要進度"
          value={sum ? sum.pct : null}
          suffix="%"
          sub={sum ? `已生成 ${nf(sum.done)} / ${nf(sum.total)}` : ''}
        />
      </SimpleGrid>

      {tag ? (
        <Card withBorder padding="md" radius="md">
          <Group justify="space-between">
            <Text fw={600}>標註 TAGGING</Text>
            <Text size="sm" c="dimmed">
              未標 {nf(tag.fail)}
            </Text>
          </Group>
          <Progress
            value={tag.pct}
            color="gold"
            mt="xs"
            role="progressbar"
            aria-label="標註進度"
            aria-valuenow={Math.round(tag.pct)}
            aria-valuemin={0}
            aria-valuemax={100}
          />
          <Text size="xs" c="dimmed" mt={4}>
            {rateText(tag.fail, rate.tpm, '標註')}
          </Text>
        </Card>
      ) : null}

      <Card withBorder padding="md" radius="md">
        <Group justify="space-between">
          <Text fw={600}>導入 INGEST</Text>
          <Text size="sm" c="dimmed">
            本輪導入 {nf(ing?.ingested ?? 0)} · 失敗 {nf(ing?.fail ?? 0)}
          </Text>
        </Group>
        <Text size="xs" c="dimmed" mt={4}>
          {ingestRateText(rate.rpm, rate.cps)}
        </Text>
      </Card>

      {sum ? (
        <Card withBorder padding="md" radius="md">
          <Group justify="space-between">
            <Text fw={600}>摘要 SUMMARY</Text>
            <Text size="sm" c="dimmed">
              未生成 {nf(sum.remaining)}
            </Text>
          </Group>
          <Progress
            value={sum.pct}
            color="gold"
            mt="xs"
            role="progressbar"
            aria-label="摘要進度"
            aria-valuenow={Math.round(sum.pct)}
            aria-valuemin={0}
            aria-valuemax={100}
          />
          <Text size="xs" c="dimmed" mt={4}>
            {rateText(sum.remaining, rate.spm, '摘要')}
          </Text>
        </Card>
      ) : null}

      <Card withBorder padding="md" radius="md">
        <Text fw={600} mb="xs">
          管線 PIPELINES
        </Text>
        <Group>
          {(['web', 'ingest', 'tag', 'summaries'] as const).map((k) => (
            <Badge key={k} color={data?.pipelines?.[k] ? 'green' : 'gray'} variant="light">
              {k} {data?.pipelines?.[k] ? '執行中' : '已停止'}
            </Badge>
          ))}
        </Group>
      </Card>

      <Card withBorder padding="md" radius="md">
        <Text fw={600} mb="xs">
          市場分佈 MARKETS
        </Text>
        <Stack gap={4}>
          {(data?.db.markets ?? []).map((m) => (
            <Group key={m.market} justify="space-between">
              <Text size="sm">{m.market}</Text>
              <Text size="sm" c="dimmed">
                {nf(m.count)}
              </Text>
            </Group>
          ))}
        </Stack>
      </Card>

      <Stack gap={6}>
        <Text size="xs" c="dimmed">
          更新於 {data?.ts ?? '—'} · 每 2 秒刷新 · 資料源 /api/progress
        </Text>
        {orch ? (
          <Group gap="xs" align="center">
            <Badge
              color={
                orch.status === 'done' ? 'green' : orch.status === 'running' ? 'yellow' : 'gray'
              }
              variant="light"
              radius="xl"
            >
              {orch.label ?? '編排器狀態'}
            </Badge>
            {orch.timestamp ? (
              <Text size="xs" c="dimmed" ff="monospace">
                {orch.timestamp}
              </Text>
            ) : orch.raw ? (
              <Text size="xs" c="dimmed" lineClamp={1} style={{ maxWidth: 520 }}>
                {orch.raw}
              </Text>
            ) : null}
          </Group>
        ) : null}
      </Stack>
    </Stack>
  )
}
