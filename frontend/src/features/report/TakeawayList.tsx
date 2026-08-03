import type { Takeaway } from '../../lib/readingSchemas'
import styles from './TakeawayList.module.css'

interface Props {
  takeaways: Takeaway[]
}

/**
 * 重點摘錄：claim ＋ 逐字引文。takeaways 為空時由呼叫端整區不渲染。
 *
 * 刻意是**非互動**元素（無 role=button、無 tabIndex、無跳轉箭頭、無 hover 底色）：
 * 閱讀頁已無文字檢視，引文沒有可跳的落點；留著互動外觀等於承諾一個按下去什麼也不會
 * 發生的動作，而且不會報錯。錨定欄位（quote_start/quote_end）仍由 extract_takeaways
 * 批次寫入、仍在 API 回應裡，只是前端不再消費 —— 那是刻意留的可逆性，別順手刪。
 */
export function TakeawayList({ takeaways }: Props) {
  return (
    <div className={styles.list}>
      {takeaways.map(t => (
        <div key={t.ordinal} className={styles.tk}>
          <div className={styles.in}>
            <span className={styles.n}>{t.ordinal}</span>
            <span className={styles.x}>
              {t.claim}
              {t.quote && <span className={styles.q}>{t.quote}</span>}
            </span>
          </div>
        </div>
      ))}
    </div>
  )
}
