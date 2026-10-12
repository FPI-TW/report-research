import { useSyncExternalStore } from 'react'
import { createPortal } from 'react-dom'
import { Icon } from '../primitives/Icon'
import { subscribeToast, toastSnapshot } from './toastStore'
import o from './Overlay.module.css'

/** toast 的畫面。整個管理後台只放一個（在 `AdminShell`）。 */
export function Toaster() {
  const t = useSyncExternalStore(subscribeToast, toastSnapshot, toastSnapshot)
  if (!t) return null
  return createPortal(
    <div key={t.id} className={o.toast} role="status" data-tone={t.tone}>
      <span className={o.toastIcon}><Icon name={t.tone === 'bad' ? 'alertCircle' : 'check'} size={15} /></span>
      {t.message}
    </div>,
    document.body,
  )
}
