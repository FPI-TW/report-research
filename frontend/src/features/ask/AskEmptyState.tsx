import type { Ref } from 'react'
import { BrandLogo } from '../../components/BrandLogo'
import { marketLabel, marketTint } from '../../lib/meta'
import { Composer } from './Composer'
import styles from './AskEmptyState.module.css'
import { t } from '@/i18n'

/** 空白頁的建議題（交接包「研究助理／空白頁」2×2）：點一下只填進輸入框，不直接送出。 */
const SUGGESTIONS: { q: string; market: string }[] = [
  { get q() { return t('ask.askEmptyState.howDoBrokersViewTsmc') }, market: 'TW' },
  { get q() { return t('ask.askEmptyState.howDoesTheUsDollar') }, market: 'FX' },
  { get q() { return t('ask.askEmptyState.whatAreTheKeyTakeaways') }, market: 'MACRO' },
  { get q() { return t('ask.askEmptyState.whatSTheLatestOn') }, market: 'CRYPTO' },
]

interface Props {
  value: string
  onChange: (v: string) => void
  onSubmit: (q: string) => void
  /** 中央輸入框外框：送出第一題時 AskPage 從這裡量起點，讓底部輸入列滑下去（FLIP）。 */
  boxRef?: Ref<HTMLDivElement>
}

/**
 * 研究助理空白頁。置中容器**只淡入、不位移**：它在高度剛好 100% 的捲動區裡，位移進場會在
 * 動畫期間撐出捲軸、可視寬度少一截，置中內容就左右跳（交接包坑 3）。
 */
export function AskEmptyState({ value, onChange, onSubmit, boxRef }: Props) {
  return (
    <div className={styles.wrap}>
      <div className={styles.inner}>
        <BrandLogo size={52} alt="" className={styles.logo} />
        <h2 className={styles.title}>{t('ask.askEmptyState.whatWouldYouLikeTo')}</h2>
        <Composer value={value} onChange={onChange} onSubmit={onSubmit} variant="center" boxRef={boxRef} />
        <div className={styles.grid}>
          {SUGGESTIONS.map((s, i) => (
            <button
              key={s.q}
              type="button"
              className={styles.sugg}
              style={{ animationDelay: `${(i + 1) * 0.04}s` }}
              onClick={() => onChange(s.q)}
            >
              <span className={styles.mkt} style={marketTint(s.market)}>{marketLabel(s.market)}</span>
              <span className={styles.q}>{s.q}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
