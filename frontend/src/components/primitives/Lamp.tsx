import styles from './Lamp.module.css'

export type LampTone = 'bad' | 'warn' | 'ok' | 'info' | 'idle'

const TONE_LABEL: Record<LampTone, string> = { bad: '異常', warn: '注意', ok: '正常', info: '進行中', idle: '閒置' }

/**
 * 燈號：6px 圓點（深色主題帶光暈，值在 tokens.css 的 `--ac-*`）。
 * 顏色不是唯一的訊號：預設帶中文的無障礙名稱；旁邊已經有文字說明狀態時傳 `label=""` 當裝飾。
 */
export function Lamp({ tone, label, size = 6, className }: { tone: LampTone; label?: string; size?: number; className?: string }) {
  const name = label ?? TONE_LABEL[tone]
  return (
    <span
      className={`${styles.lamp} ${className ?? ''}`}
      data-tone={tone}
      style={size === 6 ? undefined : { width: size, height: size }}
      {...(name ? { role: 'img', 'aria-label': name } : { 'aria-hidden': true })}
    />
  )
}
