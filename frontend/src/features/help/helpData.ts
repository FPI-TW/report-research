import { MARKET_ORDER } from '../../lib/meta'

/** 目錄與各節的錨點。id 同時是 `section` 的 DOM id（加 `help-` 前綴，避免和外殼的 id 撞名）。 */
export const HELP_TOC = [
  { id: 'start', label: '開始使用' },
  { id: 'library', label: '研報庫' },
  { id: 'reader', label: '閱讀頁' },
  { id: 'assistant', label: '研究助理' },
  { id: 'consensus', label: '券商共識' },
  { id: 'brief', label: '每日簡報' },
  { id: 'markets', label: '市場代碼' },
  { id: 'keys', label: '快捷鍵' },
  { id: 'ai', label: 'AI 生成內容' },
] as const

export type HelpSectionId = (typeof HELP_TOC)[number]['id']

export const sectionDomId = (id: HelpSectionId) => `help-${id}`

/** 市場代碼表的「範圍」欄；代碼與名稱沿用 `lib/meta`（與 findb 對齊的那一份）。 */
export const MARKET_SCOPE: Record<(typeof MARKET_ORDER)[number], string> = {
  TW: '台灣上市櫃個股與產業',
  US: '美國個股與市場，多為外資英文報告',
  HK: '香港掛牌個股與市場',
  CN: '中國 A 股與陸股策略',
  WTX: '台指期、選擇權與部位策略',
  FX: '匯率與貨幣',
  MACRO: '總經、利率與跨市場策略，債券與固定收益也歸此',
  GLOBAL: '全球資產配置，原物料、商品與能源也歸此',
  CRYPTO: '加密資產',
}

/** 修飾鍵：Mac 顯示 ⌘，其他平台顯示 Ctrl。 */
export const MOD = 'mod'

/**
 * 快捷鍵表。與實作逐條對應：全站三條在 `lib/useGlobalShortcuts.ts`（Esc 由各彈窗自理）、
 * 搜尋彈窗在 `components/shell/CommandPalette.tsx`、研究助理在 `features/ask/Composer.tsx`、
 * 閱讀頁在 `features/report/pdf/PdfViewer.tsx`。改了那邊要同步改這裡。
 */
export const HELP_KEYS: { what: string; where: string; keys: string[] }[] = [
  { what: '開啟搜尋', where: '全站', keys: [MOD, 'K'] },
  { what: '新對話', where: '全站', keys: [MOD, 'Shift', 'O'] },
  { what: '關閉彈窗或選單', where: '全站', keys: ['Esc'] },
  { what: '聚焦研報搜尋框', where: '研報庫', keys: ['/'] },
  { what: '在結果間移動', where: '搜尋', keys: ['↑', '↓'] },
  { what: '開啟選取的結果', where: '搜尋', keys: ['Enter'] },
  { what: '送出提問', where: '研究助理', keys: ['Enter'] },
  { what: '換行', where: '研究助理', keys: ['Shift', 'Enter'] },
  { what: '在 PDF 中搜尋', where: '閱讀頁', keys: [MOD, 'F'] },
  { what: '下一個搜尋結果', where: '閱讀頁', keys: ['Enter'] },
  { what: '上一個搜尋結果', where: '閱讀頁', keys: ['Shift', 'Enter'] },
  { what: '複製選取的文字', where: '閱讀頁', keys: [MOD, 'C'] },
  { what: '翻頁', where: '閱讀頁', keys: ['PageUp', 'PageDown'] },
  { what: '跳到首頁或末頁', where: '閱讀頁', keys: ['Home', 'End'] },
]
