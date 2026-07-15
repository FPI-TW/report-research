import { Composer } from './Composer'
import { BrandLogo } from '../../components/BrandLogo'
import { ModeSwitch } from '../../components/shell/ModeSwitch'
import styles from './AskEmptyState.module.css'

interface Props { value: string; onChange: (v: string) => void; onSubmit: (q: string) => void }

export function AskEmptyState({ value, onChange, onSubmit }: Props) {
  // 起始畫面各元素依序上浮（logo→標題→副標→切換鈕→輸入框），以 --tf-i 做封頂 stagger；
  // 不支援 style 的 ModeSwitch／Composer 以帶 tf-reveal 的外層 div 承載序號（Composer 外層補 width:100% 維持置中滿寬）。
  return (
    <div className={styles.wrap}>
      <BrandLogo size={56} className={`${styles.glyph} tf-reveal`} />
      <div className={`${styles.title} tf-reveal`} style={{ ['--tf-i' as string]: 1 }}>向廷豐智能體提問</div>
      <div className={`${styles.sub} tf-reveal`} style={{ ['--tf-i' as string]: 2 }}>以自然語言詢問研究主題，回答將附上券商研報的引用來源。</div>
      <div className="tf-reveal" style={{ ['--tf-i' as string]: 3 }}><ModeSwitch className={styles.switch} /></div>
      <div className={`${styles.composerReveal} tf-reveal`} style={{ ['--tf-i' as string]: 4 }}>
        <Composer value={value} onChange={onChange} onSubmit={onSubmit} variant="center" />
      </div>
    </div>
  )
}
