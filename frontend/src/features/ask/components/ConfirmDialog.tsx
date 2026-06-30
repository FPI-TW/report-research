import { Button, Group, Modal, Text } from '@mantine/core'

interface ConfirmDialogProps {
  opened: boolean
  title?: string
  body?: string
  confirmLabel?: string
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmDialog({ opened, title = '確認', body = '', confirmLabel = '確認', onConfirm, onCancel }: ConfirmDialogProps) {
  return (
    <Modal opened={opened} onClose={onCancel} title={title} centered size="sm" transitionProps={{ duration: 0 }}>
      <Text size="sm" mb="md">
        {body}
      </Text>
      <Group justify="flex-end">
        <Button variant="default" onClick={onCancel}>
          取消
        </Button>
        <Button color="red" onClick={onConfirm}>
          {confirmLabel}
        </Button>
      </Group>
    </Modal>
  )
}
