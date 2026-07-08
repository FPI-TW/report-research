import styles from './UserMessage.module.css'

export function UserMessage({ text }: { text: string }) {
  return <div className={styles.row}><div className={styles.bubble}>{text}</div></div>
}
