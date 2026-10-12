import { Button } from '../../../components/console'
import c from '../../../components/console/Console.module.css'

/** keyset 分頁（before_id）的頁尾：左邊頁碼，右邊「較新／較舊」。沒有總筆數（後端不算）。 */
export function KeysetPager({ page, note, hasOlder, onNewer, onOlder }: {
  page: number; note?: string; hasOlder: boolean; onNewer: () => void; onOlder: () => void
}) {
  return (
    <div className={c.pager}>
      <span>{`第 ${page} 頁${note ?? ''}`}</span>
      <div className={c.pagerBtns}>
        <Button size="sm" disabled={page <= 1} onClick={onNewer}>較新</Button>
        <Button size="sm" disabled={!hasOlder} onClick={onOlder}>較舊</Button>
      </div>
    </div>
  )
}
