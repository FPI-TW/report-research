import { useState } from 'react'
import { Globe, Plus, X, type LucideIcon } from 'lucide-react'
import { Popover } from '../../components/primitives/Popover'
import { setWebSearch, useWebSearch, useWebSearchPaused } from '../../lib/useWebSearch'
import styles from './ComposerTools.module.css'
import { t } from '@/i18n'

/** 一項可開關的工具。新增工具＝在 useTools() 的陣列裡多加一筆，其餘不必改。 */
export interface ComposerTool {
  key: string
  icon: LucideIcon
  name: string
  on: boolean
  toggle: () => void
}

/** 工具清單。狀態各自住在自己的 store（網搜在 useWebSearch），這裡只做組裝。
 *
 * 網搜暫停期間（`useWebSearchPaused`＝旗標 `ask.web_search` 沒開，理由與接回點見該 hook）不列 web 項：清單因此是空的，
 * `ComposerTools` 整個不渲染。殘留的 localStorage 開啟狀態也不會以膠囊外露——膠囊取自這份清單。
 */
function useTools(): ComposerTool[] {
  const web = useWebSearch()
  const paused = useWebSearchPaused()
  const tools: ComposerTool[] = []
  if (!paused) {
    tools.push({ key: 'web', icon: Globe, name: t('ask.composerTools.webSearch'), on: web, toggle: () => setWebSearch(!web) })
  }
  return tools
}

interface Props {
  /** 空白頁的中央輸入框（選單往下開、鈕小一號）或對話底部的輸入列（選單往上開）。 */
  variant?: 'center' | 'bottom'
}

/**
 * 輸入框的工具選單：一顆無框的「＋」，選起來的工具以膠囊排在它右邊。
 *
 * **已開啟的工具必須在收合狀態下就看得見**，所以狀態由膠囊承載而不是只藏在選單裡：
 * 網搜會改變答案的資料來源（研報＋外網），使用者得在按送出之前知道自己開著。
 * 膠囊本身就是關閉鈕（點一下即取消），hover 時圖示換成 ✕ 提示這件事，不另佔一個焦點。
 */
export function ComposerTools({ variant = 'bottom' }: Props) {
  const [open, setOpen] = useState(false)
  const tools = useTools()
  const active = tools.filter((item) => item.on)
  // 沒有任何工具（網搜暫停中）時不留一顆點開是空選單的「＋」；輸入框的左內距由 CSS 補上。
  if (tools.length === 0) return null
  const size = variant === 'center' ? styles.sm : styles.md

  return (
    <div className={`${styles.wrap} ${size}`}>
      <button
        type="button"
        className={styles.trigger}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t('ask.composerTools.tools')}
        title={t('ask.composerTools.tools')}
      >
        <Plus size={18} strokeWidth={1.8} aria-hidden />
      </button>
      {active.map((item) => (
        <button
          key={item.key}
          type="button"
          className={styles.chip}
          onClick={item.toggle}
          aria-pressed
          aria-label={t('ask.composerTools.turnOff', { name: item.name })}
          title={t('ask.composerTools.turnOff', { name: item.name })}
        >
          {/* 圖示與 ✕ 疊在同一格：hover 時互換而非各佔一欄，膠囊寬度一格都不動。 */}
          <span className={styles.chipIcon} aria-hidden="true">
            <span className={styles.chipIconDefault}><item.icon size={14} strokeWidth={1.8} /></span>
            <span className={styles.chipIconHover}><X size={13} strokeWidth={2} /></span>
          </span>
          <span className={styles.chipLabel}>{item.name}</span>
        </button>
      ))}
      <Popover
        open={open}
        onClose={() => setOpen(false)}
        className={`${styles.pop} ${variant === 'center' ? styles.popDown : styles.popUp}`}
        ariaLabel={t('ask.composerTools.tools')}
        openUp={variant !== 'center'}
      >
        <div className={styles.head}>{t('ask.composerTools.tools')}</div>
        {tools.map((entry) => (
          <button
            key={entry.key}
            type="button"
            role="menuitemcheckbox"
            aria-checked={entry.on}
            className={styles.item}
            onClick={entry.toggle}
          >
            <entry.icon size={16} strokeWidth={1.8} className={styles.itemIcon} aria-hidden />
            <span className={styles.itemName}>{entry.name}</span>
            <span className={`${styles.switch} ${entry.on ? styles.switchOn : ''}`} aria-hidden="true">
              <span className={styles.knob} />
            </span>
          </button>
        ))}
      </Popover>
    </div>
  )
}
