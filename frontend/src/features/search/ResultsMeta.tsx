import styles from './ResultsMeta.module.css'
export function ResultsMeta({ text }: { text: string }) {
  return <div className={styles.meta}>{text}</div>
}
