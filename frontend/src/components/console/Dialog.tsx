import { useEffect, useId, useRef, type FormEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Icon } from '../primitives/Icon'
import { useFocusTrap } from '../../lib/useFocusTrap'
import { Button } from './controls'
import c from './Console.module.css'
import o from './Overlay.module.css'

interface DialogProps {
  open: boolean
  onClose: () => void
  title: ReactNode
  children: ReactNode
  /** 面板寬度（px）。確認框 420、表單 440–460、金鑰 500。 */
  width?: number
  role?: 'dialog' | 'alertdialog'
  /** 有 × 關閉鈕（表單有；確認框沒有，只能按取消）。 */
  closeButton?: boolean
  /** 壓在其他對話框上面（重新驗證）。 */
  top?: boolean
  /** 忙碌中：Esc 與點遮罩都不關。 */
  locked?: boolean
  /** false＝只能用對話框裡的按鈕關（只顯示一次的金鑰：不能被 Esc 或點遮罩誤關）。 */
  dismissible?: boolean
}

/**
 * 主控台對話框的底：遮罩＋置中面板，portal 到 body，Esc 與點遮罩關閉，焦點鎖在面板內。
 * 一般用 `ConfirmDialog`／`FormDialog`；內容特殊（例如只顯示一次的金鑰）才直接用這個。
 */
export function Dialog({
  open, onClose, title, children, width, role = 'dialog', closeButton, top, locked: busyLocked, dismissible = true,
}: DialogProps) {
  const locked = busyLocked || !dismissible
  const panelRef = useRef<HTMLDivElement>(null)
  const titleId = useId()
  useFocusTrap(panelRef, open)

  // 焦點預設落在第一個輸入欄位（表單）；沒有欄位時留在 focus trap 選的第一顆按鈕（確認框的「取消」）。
  useEffect(() => {
    if (!open) return
    panelRef.current?.querySelector<HTMLElement>('[data-autofocus], input:not([type="checkbox"]), textarea, select')?.focus()
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || locked) return
      // 疊在上面的對話框先關：事件只給最上層那一個處理。
      const scrims = document.querySelectorAll('[data-console-scrim]')
      if (scrims[scrims.length - 1] !== panelRef.current?.parentElement) return
      e.stopPropagation()
      onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose, locked])

  if (!open) return null
  return createPortal(
    <div className={`${o.scrim} ${top ? o.scrimTop : ''}`} data-console-scrim onMouseDown={e => {
      if (e.target === e.currentTarget && !locked) onClose()
    }}>
      <div ref={panelRef} className={o.dialog} role={role} aria-modal="true" aria-labelledby={titleId}
        style={width ? { ['--dialog-w' as string]: `${width}px` } : undefined}>
        <div className={o.dialogHead}>
          <h2 id={titleId} className={o.dialogTitle}>{title}</h2>
          {closeButton && (
            <button type="button" className={c.iconBtn} onClick={onClose} title="關閉" aria-label="關閉"><Icon name="x" size={16} /></button>
          )}
        </div>
        {children}
      </div>
    </div>,
    document.body,
  )
}

/** 直接用 `Dialog` 時的內文段落（12.5px）。 */
export function DialogBody({ children }: { children: ReactNode }) {
  return <p className={o.dialogBody}>{children}</p>
}

/** 直接用 `Dialog` 時底部靠右的按鈕列。 */
export function DialogActions({ children }: { children: ReactNode }) {
  return <div className={o.dialogActions}>{children}</div>
}

interface ConfirmProps {
  open: boolean
  title: ReactNode
  body: ReactNode
  confirmLabel: string
  /** danger＝破壞性（紅色按鈕）；safe＝可逆的動作，例如恢復、發布、啟用（金色按鈕）。 */
  tone?: 'danger' | 'safe'
  busy?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/** 確認框：破壞性用紅色按鈕，可逆動作用金色按鈕。預設焦點在「取消」。 */
export function ConfirmDialog({ open, title, body, confirmLabel, tone = 'danger', busy, onConfirm, onCancel }: ConfirmProps) {
  return (
    <Dialog open={open} onClose={onCancel} title={title} width={420} role="alertdialog" locked={busy}>
      <p className={o.dialogBody}>{body}</p>
      <div className={o.dialogActions}>
        <Button onClick={onCancel} disabled={busy}>取消</Button>
        <Button variant={tone === 'safe' ? 'primary' : 'danger'} onClick={onConfirm} disabled={busy}>{confirmLabel}</Button>
      </div>
    </Dialog>
  )
}

interface FormProps {
  open: boolean
  title: ReactNode
  /** 標題下方的一段說明（12.5px）。 */
  body?: ReactNode
  submitLabel: string
  submitTone?: 'primary' | 'danger'
  /** 送出按鈕停用（必填沒填、沒有改動）。 */
  submitDisabled?: boolean
  busy?: boolean
  /** 錯誤訊息：顯示在欄位下方的紅底框（role="alert"）。 */
  error?: ReactNode
  width?: number
  /** 壓在其他對話框上面（重新驗證）。 */
  top?: boolean
  onSubmit: () => void
  onClose: () => void
  children: ReactNode
}

/** 表單對話框：標題＋ × ＋欄位（`Field` 搭配 `c.input`／`c.textarea`／`Segmented`）＋錯誤＋取消／送出。 */
export function FormDialog({
  open, title, body, submitLabel, submitTone = 'primary', submitDisabled, busy, error, width = 460, top, onSubmit, onClose, children,
}: FormProps) {
  const submit = (e: FormEvent) => { e.preventDefault(); if (!busy && !submitDisabled) onSubmit() }
  return (
    <Dialog open={open} onClose={onClose} title={title} width={width} closeButton locked={busy} top={top}>
      <form className={o.form} onSubmit={submit} noValidate>
        {body && <p className={o.dialogBody}>{body}</p>}
        {children}
        {error && <p className={c.error} role="alert">{error}</p>}
        <div className={o.dialogActions}>
          <Button onClick={onClose} disabled={busy}>取消</Button>
          <Button type="submit" variant={submitTone} disabled={busy || submitDisabled}>{submitLabel}</Button>
        </div>
      </form>
    </Dialog>
  )
}
