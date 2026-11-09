import { useEffect, useRef } from 'react'
import type { BulkOutcome } from './useSelection'
import c from '../../components/console/Console.module.css'
import { t } from '@/i18n'

/** 表頭的「全選本頁」：全部已選＝勾、部分已選＝半勾（indeterminate）、沒有可選的列＝停用。 */
export function SelectAllBox({ ids, selected, onChange, label = t('admin.bulkParts.selectAllOnThisPage') }: {
  ids: readonly string[]; selected: ReadonlySet<string>; onChange: (on: boolean) => void; label?: string
}) {
  const ref = useRef<HTMLInputElement>(null)
  const count = ids.filter(id => selected.has(id)).length
  const all = ids.length > 0 && count === ids.length
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = count > 0 && !all
  }, [count, all])
  return (
    <input ref={ref} type="checkbox" className={c.check} aria-label={label} checked={all}
      disabled={ids.length === 0} onChange={e => onChange(e.target.checked)} />
  )
}

/** 送出批次後的逐筆結果：成功幾筆、未變更幾筆、略過幾筆與各自的原因。 */
export function BulkResultPanel({ outcome, onClose }: { outcome: BulkOutcome | null; onClose: () => void }) {
  if (!outcome) return null
  const { title, ok, unchanged = 0, skipped } = outcome
  const parts = [t('admin.bulkParts.succeeded', { ok })]
  if (unchanged) parts.push(t('admin.bulkParts.unchangedAlreadyInTheTarget', { unchanged }))
  parts.push(t('admin.bulkParts.skipped', { length: skipped.length }))
  return (
    <section className={c.notice} aria-label={t('admin.bulkParts.batchResult')} role="status">
      <div className={c.noticeHead}>
        <strong>{t('admin.bulkParts.text', { title })}{parts.join(t('admin.bulkParts.text2'))}</strong>
        <button type="button" className={`${c.btn} ${c.btnSm}`} onClick={onClose}>{t('admin.bulkParts.close')}</button>
      </div>
      {skipped.length > 0 && (
        <ul className={c.noticeList}>
          {skipped.map((s, i) => <li key={i}><strong>{s.name}</strong>{t('admin.bulkParts.text3', { detail: s.detail })}</li>)}
        </ul>
      )}
    </section>
  )
}
