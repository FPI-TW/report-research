import { Modal } from './Modal'
import { Pressable } from './Pressable'
import styles from './ConfirmDialog.module.css'

interface Props { open: boolean; title: string; body: string; confirmLabel: string; onConfirm: () => void; onCancel: () => void }

export function ConfirmDialog({ open, title, body, confirmLabel, onConfirm, onCancel }: Props) {
  return (
    <Modal open={open} onClose={onCancel} title={title} className={styles.panel}>
      <p className={styles.body}>{body}</p>
      <div className={styles.actions}>
        <Pressable className={styles.cancel} onClick={onCancel}>取消</Pressable>
        <Pressable className={styles.confirm} onClick={onConfirm}>{confirmLabel}</Pressable>
      </div>
    </Modal>
  )
}
