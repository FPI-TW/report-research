import { useEffect, useState } from 'react'
import { Alert, Anchor, Loader, Modal, Stack, Text } from '@mantine/core'
import { z } from 'zod'
import { getJSON } from '../../../lib/api'
import { fmtDate, mLabel, tLabel } from './meta'

// `/api/report/{id}/full` 回傳的 schema（僅 metadata，無全文）
const fullReportSchema = z.object({
  report_id: z.string(),
  file_name: z.string(),
  market: z.string().nullish(),
  source: z.string().nullish(),
  summary: z.string().nullish(),
  report_date: z.string().nullish(),
  report_type: z.string().nullish(),
  has_file: z.boolean().nullish(),
})

type FullReport = z.infer<typeof fullReportSchema>

export interface ReportDetailModalProps {
  reportId: string | null
  onClose: () => void
}

/**
 * 最小研報詳情 Modal（Phase 2a）。
 * 開啟時 fetch `/api/report/{id}/full`，純文字渲染 metadata。
 * 嵌入 PDF 為 Phase 4，此處不實作。
 */
export function ReportDetailModal({ reportId, onClose }: ReportDetailModalProps) {
  const [data, setData] = useState<FullReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!reportId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setData(null)
      setError(null)
      return
    }

    let cancelled = false
    setLoading(true)
    setData(null)
    setError(null)

    getJSON(`/api/report/${reportId}/full`, fullReportSchema)
      .then((result) => {
        if (!cancelled) setData(result)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : '載入失敗')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [reportId])

  const dateStr = fmtDate(data?.report_date)

  return (
    <Modal
      opened={reportId != null}
      onClose={onClose}
      title={data?.file_name ?? '研報詳情'}
      size="lg"
    >
      {loading && (
        <div data-testid="modal-loading">
          <Loader size="sm" />
        </div>
      )}
      {error && (
        <div data-testid="modal-error">
          <Alert color="red">{error}</Alert>
        </div>
      )}
      {data && (
        <Stack gap="xs" data-testid="modal-body">
          {data.market && (
            <Text size="sm">
              <strong>市場：</strong>
              {mLabel(data.market)}
            </Text>
          )}
          {data.source && (
            <Text size="sm">
              <strong>來源：</strong>
              {data.source}
            </Text>
          )}
          {dateStr && (
            <Text size="sm">
              <strong>日期：</strong>
              {dateStr}
            </Text>
          )}
          {data.report_type && (
            <Text size="sm">
              <strong>類型：</strong>
              {tLabel(data.report_type)}
            </Text>
          )}
          {data.summary && (
            <Text size="sm" data-testid="modal-summary">
              <strong>摘要：</strong>
              {data.summary}
            </Text>
          )}
          {data.has_file && (
            <Anchor
              href={`/api/report/${reportId}/file`}
              target="_blank"
              rel="noopener noreferrer"
              data-testid="modal-file-link"
            >
              開啟原始檔
            </Anchor>
          )}
        </Stack>
      )}
    </Modal>
  )
}
