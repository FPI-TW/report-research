import { Modal } from './Modal'
import styles from './ConfirmDialog.module.css'

interface Props { open: boolean; title: string; body: string; confirmLabel: string; onConfirm: () => void; onCancel: () => void }

export function ConfirmDialog({ open, title, body, confirmLabel, onConfirm, onCancel }: Props) {
  return (
    <Modal open={open} onClose={onCancel} title={title}>
      <p className={styles.body}>{body}</p>
      <div className={styles.actions}>
        <button type="button" className={styles.cancel} onClick={onCancel}>取消</button>
        <button type="button" className={styles.confirm} onClick={onConfirm}>{confirmLabel}</button>
      </div>
    </Modal>
  )
}
