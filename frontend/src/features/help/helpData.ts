import { MARKET_ORDER } from '../../lib/meta'
import { t } from '@/i18n'

/** 目錄與各節的錨點。id 同時是 `section` 的 DOM id（加 `help-` 前綴，避免和外殼的 id 撞名）。 */
export const HELP_TOC = [
  { id: 'start', get label() { return t('help.helpData.gettingStarted') } },
  { id: 'library', get label() { return t('help.helpData.library') } },
  { id: 'reader', get label() { return t('help.helpData.readingPage') } },
  { id: 'assistant', get label() { return t('help.helpData.assistant') } },
  { id: 'consensus', get label() { return t('help.helpData.consensus') } },
  { id: 'brief', get label() { return t('help.helpData.dailyBrief') } },
  { id: 'markets', get label() { return t('help.helpData.marketCodes') } },
  { id: 'keys', get label() { return t('help.helpData.keyboardShortcuts') } },
  { id: 'ai', get label() { return t('help.helpData.aiGeneratedContent') } },
] as const

export type HelpSectionId = (typeof HELP_TOC)[number]['id']

export const sectionDomId = (id: HelpSectionId) => `help-${id}`

/** 市場代碼表的「範圍」欄；代碼與名稱沿用 `lib/meta`（標準市場代碼）。 */
export const MARKET_SCOPE: Record<(typeof MARKET_ORDER)[number], string> = {
  get TW() { return t('help.helpData.taiwanListedAndOtcStocks') },
  get US() { return t('help.helpData.usStocksAndMarketsMostly') },
  get HK() { return t('help.helpData.hongKongListedStocksAnd') },
  get CN() { return t('help.helpData.chinaASharesAndMainland') },
  get WTX() { return t('help.helpData.taiexFuturesOptionsAndPositioning') },
  get FX() { return t('help.helpData.currenciesAndFx') },
  get MACRO() { return t('help.helpData.macroRatesAndCrossMarket') },
  get GLOBAL() { return t('help.helpData.globalAssetAllocationIncludingRaw') },
  get CRYPTO() { return t('help.helpData.cryptoAssets') },
}

/** 修飾鍵：Mac 顯示 ⌘，其他平台顯示 Ctrl。 */
export const MOD = 'mod'

/**
 * 快捷鍵表。與實作逐條對應：全站三條在 `lib/useGlobalShortcuts.ts`（Esc 由各彈窗自理）、
 * 搜尋彈窗在 `components/shell/CommandPalette.tsx`、研究助理在 `features/ask/Composer.tsx`、
 * 閱讀頁在 `features/report/pdf/PdfViewer.tsx`。改了那邊要同步改這裡。
 */
export const HELP_KEYS: { what: string; where: string; keys: string[] }[] = [
  { get what() { return t('help.helpData.openSearch') }, get where() { return t('help.helpData.everywhere') }, keys: [MOD, 'K'] },
  { get what() { return t('help.helpData.newConversation') }, get where() { return t('help.helpData.everywhere') }, keys: [MOD, 'Shift', 'O'] },
  { get what() { return t('help.helpData.closeDialogOrMenu') }, get where() { return t('help.helpData.everywhere') }, keys: ['Esc'] },
  { get what() { return t('help.helpData.focusReportSearchBox') }, get where() { return t('help.helpData.library') }, keys: ['/'] },
  { get what() { return t('help.helpData.moveBetweenResults') }, get where() { return t('help.helpData.search') }, keys: ['↑', '↓'] },
  { get what() { return t('help.helpData.openSelectedResult') }, get where() { return t('help.helpData.search') }, keys: ['Enter'] },
  { get what() { return t('help.helpData.sendQuestion') }, get where() { return t('help.helpData.assistant') }, keys: ['Enter'] },
  { get what() { return t('help.helpData.newLine') }, get where() { return t('help.helpData.assistant') }, keys: ['Shift', 'Enter'] },
  { get what() { return t('help.helpData.searchInPdf') }, get where() { return t('help.helpData.readingPage') }, keys: [MOD, 'F'] },
  { get what() { return t('help.helpData.nextSearchResult') }, get where() { return t('help.helpData.readingPage') }, keys: ['Enter'] },
  { get what() { return t('help.helpData.previousSearchResult') }, get where() { return t('help.helpData.readingPage') }, keys: ['Shift', 'Enter'] },
  { get what() { return t('help.helpData.copySelectedText') }, get where() { return t('help.helpData.readingPage') }, keys: [MOD, 'C'] },
  { get what() { return t('help.helpData.turnPage') }, get where() { return t('help.helpData.readingPage') }, keys: ['PageUp', 'PageDown'] },
  { get what() { return t('help.helpData.jumpToFirstOrLast') }, get where() { return t('help.helpData.readingPage') }, keys: ['Home', 'End'] },
]
