import { useEffect, useRef, useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { copyText } from '../../lib/clipboard'
import { showToast } from '../../lib/toast'

// 複製成功後綠勾停留的時間（交接包「互動／複製」）。
const COPIED_MS = 1500

interface Props {
  text: string
  label: string
  className: string
  checkClassName: string
}

/** 圖示複製鈕：成功時圖示換成綠勾 1.5 秒；失敗（非安全情境退路也不行）時跳提示，不靜默。 */
export function CopyIconButton({ text, label, className, checkClassName }: Props) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])

  const onClick = () => {
    copyText(text).then(
      () => {
        window.clearTimeout(timer.current)
        setCopied(true)
        timer.current = window.setTimeout(() => setCopied(false), COPIED_MS)
      },
      () => showToast('無法複製，請手動選取文字'),
    )
  }

  return (
    <button type="button" className={className} onClick={onClick} aria-label={label} title={label}>
      {copied
        ? <Check key="ok" size={15} strokeWidth={2} className={checkClassName} aria-hidden />
        : <Copy key="copy" size={15} strokeWidth={1.8} aria-hidden />}
    </button>
  )
}
