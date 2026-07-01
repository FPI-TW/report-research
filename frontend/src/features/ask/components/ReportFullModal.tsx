import type { JSX } from 'react'
import { Alert, Box, Button, Loader, Modal, Stack } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { getReportFull } from '../api'
import { renderReport } from '../lib/reportMarkdown'

interface ReportFullModalProps {
  reportId: string | null
  opened: boolean
  onClose: () => void
}

/**
 * 深度研報全文檢視 modal。`opened` 與 `reportId` 分離（呼叫端可各自控制），
 * 兩者皆滿足才觸發查詢。`transitionProps={{ duration: 0 }}` 讓 jsdom 測試同步掛載。
 */
export function ReportFullModal({ reportId, opened, onClose }: ReportFullModalProps): JSX.Element {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['report-full', reportId],
    queryFn: () => getReportFull(reportId!),
    enabled: opened && reportId != null,
  })

  return (
    <Modal opened={opened} onClose={onClose} title={data?.title ?? '研報全文'} size="lg" transitionProps={{ duration: 0 }}>
      <Box data-testid="report-full-modal">
        {isLoading && <Loader data-testid="report-full-loading" size="sm" />}
        {isError && (
          <Stack gap="xs" data-testid="report-full-error">
            <Alert color="red" title="研報載入失敗">
              研報載入失敗，請稍後再試。
            </Alert>
            <Button size="xs" variant="light" onClick={() => refetch()}>
              重試
            </Button>
          </Stack>
        )}
        {data && <div data-testid="report-full-body">{renderReport(data.markdown)}</div>}
      </Box>
    </Modal>
  )
}
