import atoms from './atoms.module.css'
import styles from './LoadMore.module.css'
import { t } from '@/i18n'

interface Props { remaining: number; loading: boolean; onClick: () => void }

export function LoadMore({ remaining, loading, onClick }: Props) {
  return (
    <div className={styles.wrap}>
      <button type="button" className={atoms.btn} disabled={loading} onClick={onClick}>
        {loading ? t('search.loadMore.loading') : <>{t('search.loadMore.loadMore')}{' '}<span className={atoms.num}>{remaining.toLocaleString()}</span>{' '}{t('search.loadMore.remaining')}</>}
      </button>
    </div>
  )
}
