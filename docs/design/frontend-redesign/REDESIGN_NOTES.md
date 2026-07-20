# 重設計實作說明（1a 墨青 × 鎏金）

依「設計提案.dc.html」選定方向 1a 實作。**只動 UI／樣式層**：資料流、hooks（useAskController、useSearchResults…）、API 契約、路由、元件名與 props 全部不變。

## 設計語言
- **墨青（--tf-ink #1E5175）**＝導航與操作：按鈕、連結、選取態、focus ring、相關度。
- **鎏金（--tf-gold-* #8A5A0F/#AE7415）**＝引用與研報：引註 [n]、來源編號、深度研報面板、串流游標、Logo。
- 字體：標題／報告名 Noto Serif TC；介面 Noto Sans TC（index.html 已加載）；數字 tabular-nums。
- 市場 chip 一律「淡底深字」（meta.ts 新增 `marketTint()`，color-mix 10% 混白）。

## 變更檔案
**Token／基礎**
- `index.html`：加 Noto Sans TC。
- `src/styles/tokens.css`：全新色票（基調暖紙白、墨青、鎏金、市場色降彩度）、圓角/陰影/焦點環；變數名向下相容並新增 `--tf-ink-*`。
- `src/lib/meta.ts`：市場色重校＋`marketTint()`。

**Shell**
- SideRail／NavItem／ConversationList／AccountMenu `.module.css`：選取態改墨青（tint 底＋左緣 3px 指示條）、新對話鈕、帳號頭像改墨青。

**檢索（結構性變更僅此頁）**
- `SearchPage.tsx`：空查詢＋無篩選 → 品牌起始畫面（logo＋宋體字標＋檢索/問答切換＋大型搜尋框＋「試試」建議查詢），下方即「最新入庫」（就是原 browse 結果，資料流不變）；ViewSwitch 由右下浮動移入工具列。
- `SearchBar.tsx`：新增 `size="lg"`（hero 大框＋「搜尋」鈕），md 態不變。
- `ResultCard.tsx/.module.css`：2 欄卡片 → 高密度列表列（市場｜標題+標的+命中片段｜相關度+來源日期）。
- `CardsView.tsx/.module.css`＋`MonthGroup.module.css`：白底容器＋月份 sticky 窄條群組標頭。
- `ViewSwitch.tsx/.module.css`：分段控制；aria-label「卡片檢視」→「列表檢視」。
- MarketChipBar／SortMenu／MoreFiltersPopover／ActiveChips／EmptyState／LoadMore／ResultsMeta／TableView：改樣式與 token（TableView 徽章改淡底深字）。

**問答**
- UserMessage：氣泡改墨青。AssistantMessage：引註 pill 維持鎏金（hover 反白）、游標鎏金、動作列 hover 墨青。
- `components/CitePreview.tsx`（新）：引註 [n] hover／focus 即時來源預覽卡（市場 chip＋日期＋報告名）。純展示、不攔截點擊；點擊仍開來源抽屜；貼近視窗下緣自動上翻，捲動即關閉。`renderAnswer()` 新增「選用」第 4 參數 `sources`（未傳行為同舊版，既有測試不受影響）。
- ThinkingSteps：收成一行膠囊摘要（展開邏輯不變）。
- Composer：送出鈕墨青＋底部 AI 聲明（bottom 變體）。
- DeepReportPanel：邀請/生成/完成改鎏金面板；文案「生成研報／暫時不用」。
- SourcesDrawer：分「研報 · n／網路補充 · m」兩節、標頭計數 chip、市場 chip 淡底深字。

**Modal**
- Modal.module.css：圓角 16、墨青遮罩、圓形關閉鈕。
- ReportDetailModal：PDF 態頂部加動作列（在新分頁開啟＝主按鈕、下載原始檔＝ghost、Esc 提示）。

## 已知待辦（不影響編譯）
- 測試文案斷言需同步：DeepReportPanel「要／不用」→「生成研報／暫時不用」；SourcesDrawer「資料來源 · N」→「研報 · N」；ViewSwitch aria-label「卡片檢視」→「列表檢視」。
- SearchSkeleton 仍是舊卡片形骨架，建議之後改列骨架。
- Monitor 頁未改版，僅繼承新 token。
- 換 1b 松墨：改 tokens.css 四值 `--tf-ink: #1E5B4B; --tf-ink-deep: #14453A; --tf-ink-hover: #17493C; --tf-ink-tint: #E7F0EC;`（另 `--tf-ink-line: #D8E6E0`）。

## 無障礙
- 主色對白底 7.4:1（AAA）、gold-text 6.4:1、text-3 4.6:1（AA）。
- `:focus-visible` 全域墨青 2px 外框；列表列 focus 有內側指示；Esc／鍵盤操作沿用原行為。
- `marketTint()` 使用 color-mix（需較新瀏覽器；Vite target 預設 esnext 無礙）。
