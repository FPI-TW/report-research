import { useState } from 'react'
import { Popover } from '../../components/primitives/Popover'
import { Pressable } from '../../components/primitives/Pressable'
import { Icon, type IconName } from '../../components/primitives/Icon'
import { setWebSearch, useWebSearch, WEB_SEARCH_PAUSED } from '../../lib/useWebSearch'
import styles from './ComposerTools.module.css'

/** 一項可開關的工具。新增工具＝在 useTools() 的陣列裡多加一筆，其餘不必改。 */
export interface ComposerTool {
  key: string
  icon: IconName
  name: string
  on: boolean
  toggle: () => void
}

/** 工具清單。狀態各自住在自己的 store（網搜在 useWebSearch），這裡只做組裝。
 *
 * 網搜暫停期間（`WEB_SEARCH_PAUSED`，理由與接回點見該常數）不列 web 項：清單因此是空的，
 * `ComposerTools` 整個不渲染。殘留的 localStorage 開啟狀態也不會以膠囊外露——膠囊取自這份清單。
 */
function useTools(): ComposerTool[] {
  const web = useWebSearch()
  const tools: ComposerTool[] = []
  if (!WEB_SEARCH_PAUSED) {
    tools.push({ key: 'web', icon: 'globe', name: '網路搜尋', on: web, toggle: () => setWebSearch(!web) })
  }
  return tools
}

/**
 * 輸入框最左的工具選單（版面參考 ChatGPT）：一顆無框的「＋」，選起來的工具以膠囊
 * 排在它右邊、與它之間隔一道髮絲線。
 *
 * **已開啟的工具必須在收合狀態下就看得見**，所以狀態由膠囊承載而不是只藏在選單裡：
 * 網搜會改變答案的資料來源（研報＋外網），使用者得在按送出之前知道自己開著。
 * 靜態時只是圖示＋文字（金色），膠囊外形與 ✕ 都留到 hover／focus 才出現：
 * 它本身就是關閉鈕（點一下即取消），✕ 只是提示，不另佔一個焦點。
 */
export function ComposerTools() {
  const [open, setOpen] = useState(false)
  const tools = useTools()
  const active = tools.filter((t) => t.on)
  // 沒有任何工具（網搜暫停中）時不留一顆點開是空選單的「＋」；輸入框的左內距由 CSS 補上。
  if (tools.length === 0) return null

  return (
    <div className={styles.wrap}>
      <Pressable
        className={styles.trigger}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="工具"
        title="工具"
      >
        <Icon name="plus" size={18} />
      </Pressable>
      {active.length > 0 && <span className={styles.divider} aria-hidden="true" />}
      {active.map((t) => (
        <Pressable
          key={t.key}
          hoverScale={1}
          className={styles.chip}
          onClick={t.toggle}
          aria-pressed
          aria-label={`關閉${t.name}`}
          title={`關閉${t.name}`}
        >
          {/* 圖示與 ✕ 疊在同一格：hover 時互換而非各佔一欄，膠囊右側就不必為一個
              平時看不見的 ✕ 留位（那正是它看起來右邊空一截的原因）。 */}
          <span className={styles.chipIcon} aria-hidden="true">
            <span className={styles.chipIconDefault}><Icon name={t.icon} size={15} /></span>
            <span className={styles.chipIconHover}><Icon name="x" size={14} /></span>
          </span>
          <span className={styles.chipLabel}>{t.name}</span>
        </Pressable>
      ))}
      <Popover open={open} onClose={() => setOpen(false)} className={styles.pop} ariaLabel="工具" openUp>
        <div className={styles.head}>工具</div>
        {tools.map((t) => (
          <button
            key={t.key}
            type="button"
            role="menuitemcheckbox"
            aria-checked={t.on}
            className={styles.item}
            onClick={t.toggle}
          >
            <span className={styles.itemIcon}><Icon name={t.icon} size={17} /></span>
            <span className={styles.itemName}>{t.name}</span>
            <span className={`${styles.switch} ${t.on ? styles.switchOn : ''}`} aria-hidden="true">
              <span className={styles.knob} />
            </span>
          </button>
        ))}
      </Popover>
    </div>
  )
}
