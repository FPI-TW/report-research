import { useState, type JSX } from 'react'
import { Alert, Anchor, Button, Card, Group, Stack, Text } from '@mantine/core'
import type { TurnState } from '../lib/conversation'
import type { ReportState } from '../hooks/useReportStream'
import type { ReportStage } from '../lib/reportEvents'
import { renderReport } from '../lib/reportMarkdown'

/** stage 中文對映，對齊 vanilla ask.js maybeOfferReport/renderReportResult 語意。 */
const REPORT_STAGE: Record<ReportStage, string> = {
  retrieving: '深度檢索研報中…',
  searching_web: '搜尋網路補充…',
  writing: '撰寫研報中…',
  rendering: '排版 PDF 中…',
}

function stageLabel(stage: ReportStage | null): string {
  if (!stage) return '生成中…'
  return REPORT_STAGE[stage] ?? '生成中…'
}

/** 後端固定回傳同源相對路徑（/api/report-doc/{id}/pdf），僅允許 http(s) 絕對網址或同源根相對路徑，
 * 防禦性阻擋 javascript:/data: 等危險 scheme（縱使目前後端不會產生，作為深度防禦）。 */
const SAFE_DOWNLOAD_URL = /^(?:https?:\/\/|\/)/

function isSafeDownloadUrl(url: string): boolean {
  return SAFE_DOWNLOAD_URL.test(url)
}

interface ReportPanelProps {
  turn: TurnState
  report: ReportState
  onStart: () => void
  onDismiss: () => void
  onOpenFull: (reportId: string) => void
}

/**
 * 深度研報面板：對齊 vanilla ask.js 的 .ask-report-host（offer/生成/完成/失敗）
 * 並擴充歷史重播（turn.reports）與查看全文。優先序：
 * 1. 歷史重播（turn.reports）
 * 2. 本輪進行中（report.turnId===turn.id）：generating/done/error
 * 3. 主動建議（turn.offerReport 且未 dismiss）
 * 4. 無 → null
 */
export function ReportPanel({ turn, report, onStart, onDismiss, onOpenFull }: ReportPanelProps): JSX.Element | null {
  const [dismissed, setDismissed] = useState(false)

  if (turn.reports && turn.reports.length > 0) {
    return (
      <Stack gap="sm" data-testid="ask-report-host">
        {turn.reports.map((r) => (
          <ReportDoneCard
            key={r.report_id}
            title={r.title ?? turn.reportTitle ?? '深度研報'}
            downloadUrl={r.download_url}
            onOpenFull={() => onOpenFull(r.report_id)}
          />
        ))}
      </Stack>
    )
  }

  if (report.turnId === turn.id) {
    if (report.phase === 'generating') {
      return (
        <Card withBorder radius="md" p="md" data-testid="report-generating">
          <Stack gap="sm">
            <Text size="sm" c="dimmed" data-testid="report-status">
              {stageLabel(report.stage)}
            </Text>
            <div data-testid="report-preview" aria-live="polite">
              {renderReport(report.markdown)}
            </div>
          </Stack>
        </Card>
      )
    }

    if (report.phase === 'done' && report.done) {
      const done = report.done
      return (
        <ReportDoneCard
          title={done.title}
          downloadUrl={done.download_url}
          onOpenFull={() => onOpenFull(done.report_id)}
        />
      )
    }

    if (report.phase === 'error') {
      return (
        <Alert color="red" title="研報生成失敗" data-testid="report-failed">
          <Stack gap="sm">
            <Text size="sm">{report.error ?? '研報生成失敗'}</Text>
            <Button data-testid="report-retry" size="xs" variant="light" onClick={onStart}>
              重試
            </Button>
          </Stack>
        </Alert>
      )
    }
  }

  if (turn.offerReport && !dismissed) {
    return (
      <Card withBorder radius="md" p="md" data-testid="report-offer">
        <Stack gap="sm">
          <Text size="sm">要不要我幫你整理成一份完整 PDF 研報？</Text>
          <Group gap="sm">
            <Button data-testid="report-offer-yes" size="xs" onClick={onStart}>
              要
            </Button>
            <Button
              data-testid="report-offer-no"
              size="xs"
              variant="default"
              onClick={() => {
                setDismissed(true)
                onDismiss()
              }}
            >
              不用
            </Button>
          </Group>
        </Stack>
      </Card>
    )
  }

  return null
}

function ReportDoneCard({
  title,
  downloadUrl,
  onOpenFull,
}: {
  title: string
  downloadUrl: string
  onOpenFull: () => void
}): JSX.Element {
  const safe = isSafeDownloadUrl(downloadUrl)
  return (
    <Card withBorder radius="md" p="md" data-testid="report-done">
      <Stack gap="sm">
        <Text fw={700} size="sm">
          {title}
        </Text>
        <Group gap="sm">
          {safe ? (
            <Anchor href={downloadUrl} download data-testid="report-download">
              下載 PDF
            </Anchor>
          ) : (
            <Button data-testid="report-download" size="xs" variant="default" disabled>
              下載 PDF（連結不安全）
            </Button>
          )}
          <Button data-testid="report-viewfull" size="xs" variant="light" onClick={onOpenFull}>
            查看全文
          </Button>
        </Group>
      </Stack>
    </Card>
  )
}
