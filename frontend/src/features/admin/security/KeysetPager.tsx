import { Button } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { t } from '@/i18n'

/** keyset 分頁（before_id）的頁尾：左邊頁碼，右邊「較新／較舊」。沒有總筆數（後端不算）。 */
export function KeysetPager({ page, note, hasOlder, onNewer, onOlder }: {
  page: number; note?: string; hasOlder: boolean; onNewer: () => void; onOlder: () => void
}) {
  return (
    <div className={c.pager}>
      <span>{t('admin.security.keysetPager.page', { page, v: note ?? '' })}</span>
      <div className={c.pagerBtns}>
        <Button size="sm" disabled={page <= 1} onClick={onNewer}>{t('admin.security.keysetPager.newer')}</Button>
        <Button size="sm" disabled={!hasOlder} onClick={onOlder}>{t('admin.security.keysetPager.older')}</Button>
      </div>
    </div>
  )
}
