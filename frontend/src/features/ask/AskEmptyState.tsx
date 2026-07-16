import { Composer } from './Composer'
import { BrandLogo } from '../../components/BrandLogo'
import { ModeSwitch } from '../../components/shell/ModeSwitch'
import { Reveal } from '../../components/primitives/Reveal'
import styles from './AskEmptyState.module.css'

interface Props { value: string; onChange: (v: string) => void; onSubmit: (q: string) => void }

export function AskEmptyState({ value, onChange, onSubmit }: Props) {
  return (
    <Reveal className={styles.wrap}>
      <BrandLogo size={56} className={styles.glyph} />
      <div className={styles.title}>向廷豐智能體提問</div>
      <div className={styles.sub}>以自然語言詢問研究主題，回答將附上券商研報的引用來源。</div>
      <ModeSwitch className={styles.switch} />
      <Composer value={value} onChange={onChange} onSubmit={onSubmit} variant="center" />
    </Reveal>
  )
}
