import { Modal } from './Modal'
import { Pressable } from './Pressable'
import styles from './ConfirmDialog.module.css'
import { t } from '@/i18n'

interface Props { open: boolean; title: string; body: string; confirmLabel: string; onConfirm: () => void; onCancel: () => void }

export function ConfirmDialog({ open, title, body, confirmLabel, onConfirm, onCancel }: Props) {
  return (
    <Modal open={open} onClose={onCancel} title={title} className={styles.panel}>
      <p className={styles.body}>{body}</p>
      <div className={styles.actions}>
        <Pressable className={styles.cancel} onClick={onCancel}>{t('primitives.confirmDialog.cancel')}</Pressable>
        <Pressable className={styles.confirm} onClick={onConfirm}>{confirmLabel}</Pressable>
      </div>
    </Modal>
  )
}
