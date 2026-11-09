# 介面譯名表（zh-Hant → en）

英文字典（`messages/en/*.ts`）與登入頁（`web/static/login.i18n.json`）的固定譯名。新增或改寫英文字串時照這張表；
表上沒有的詞，先查同一個字典裡既有的寫法，再決定要不要加進來。

## 原則

- 介面用語簡短、句首大寫（sentence case），按鈕與選單是動詞開頭（Save、Export CSV）。
- 不用「·」當分隔符（產品 UI 規則；`i18n.test.ts` 守英文字典）：中文的「・」在英文改成逗號或分號。
- 選項說明與提示避免不必要的「you」，用祈使句或無主詞的寫法（Enter the code…、Requires re-verification）。
- 研報內容、LLM 產生的摘要／標題／摘錄、使用者輸入一律不翻（資料不是介面）；券商、標的名稱照資料原樣顯示。
- 機構名與產品名不寫在字典裡：來自 profile（`[institution]` 的 `*_en`，沒給就用原名）。
- 單複數寫成 `{ one, other }`；中文不需要。

## 產品與頁面

| 中文 | English | 備註 |
|---|---|---|
| 研報庫 | Library | 導覽與頁名；網址 `/library` |
| 研究助理 | Assistant | 網址 `/assistant` |
| 券商共識 | Consensus | 網址 `/consensus`；內文可寫 broker consensus |
| 觀點雷達 | Viewpoint radar | 登入頁功能磚、說明頁 |
| 每日簡報 | Daily brief | 「今日簡報」＝Today's brief |
| 閱讀頁 | Reading page | |
| 管理後台 | Admin console | |
| 使用說明 | Help | |
| 研報平台 | Research platform | 管理後台回到一般頁面的連結 |

## 研報與金融用語

| 中文 | English | 備註 |
|---|---|---|
| 研報、研究報告 | Research report（簡稱 report） | |
| 券商 | Broker | |
| 評等 | Rating | 評等變動＝rating change；調升／調降＝upgrade／downgrade（動詞 upgraded／downgraded） |
| 目標價 | Target price | 上修／下修＝raised／cut |
| 共識評等 | Consensus rating | 目標價中位數＝median target price |
| 買進／加碼／中立／減碼／賣出 | Buy／Overweight／Neutral／Underweight／Sell | 未評等＝Not rated |
| 偏多／中立／偏空 | Bullish／Neutral／Bearish | 立場＝stance |
| 四向觀點 | Four-way view | 展望／催化劑／風險／估值＝Outlook／Catalysts／Risks／Valuation |
| 轉強／轉弱／分歧擴大／無顯著變化 | Stronger／Weaker／Wider divergence／No significant change | |
| 標的 | Instrument | 標的代號＝ticker |
| 市場 | Market | 代碼見下表 |
| 商品類型 | Asset class | equity／index／futures／options／bonds／commodities／FX／crypto |
| 報告類型 | Report type | |
| 重點摘錄 | Key takeaways | 單筆＝key takeaway |
| 摘要 | Summary | |
| 引用來源 | Sources | 引用編號＝citation number |
| 相似研報 | Similar reports | |
| 原始檔、原檔 | Original file（PDF 時 original PDF） | |
| 入庫 | Ingested | 最新入庫＝recently ingested |
| 語料 | Corpus | |
| 忠實度 | Faithfulness | 判定尺＝judge；低分門檻＝low-score threshold |
| 檢索 | Search（技術語境 retrieval） | 檢索回歸＝retrieval regression |
| 問答 | Q&A | |

## 市場代碼的顯示名稱

| 代碼 | 中文 | English |
|---|---|---|
| TW | 台股 | Taiwan |
| US | 美股 | US |
| HK | 港股 | Hong Kong |
| CN | 陸股 | China |
| WTX | 台指期 | TAIEX futures |
| FX | 外匯 | FX |
| MACRO | 總經 | Macro |
| GLOBAL | 全球 | Global |
| CRYPTO | 加密 | Crypto |

profile 的 `[markets].display_names` 是中文顯示名稱的覆寫；英文介面一律用這張表的名稱。

## 帳號與管理

| 中文 | English | 備註 |
|---|---|---|
| 帳號 | Account（欄位名 Username） | |
| 管理員／一般使用者 | Admin／Regular user | super admin 照寫 |
| 兩步驟驗證 | Two-step verification | 驗證碼＝verification code；驗證器＝authenticator |
| 重新驗證身分 | Confirm identity／re-verify | 權限提升＝elevation |
| 公司帳號（SSO） | Company account (SSO) | 以公司帳號登入＝Sign in with company account |
| 緊急管理員 | Emergency administrator | break-glass |
| 待複核 | Review queue | 處理＝handle；倒讚＝thumbs-down |
| 操作紀錄 | Audit log | 稽核鏈＝audit chain；錨定＝anchor |
| 功能旗標 | Feature flags | 環境上限＝environment ceiling；覆寫＝override |
| 配額 | Quotas | 影子模式＝shadow mode；正式阻擋＝enforced |
| API 用戶端 | API client | 授權範圍＝access scope；限流＝rate limit；每日額度＝daily quota；輪替＝rotate |
| 上傳研報 | Upload reports | 草稿＝draft；發布＝publish；退回＝reject；撤銷退回＝undo rejection；寬限期＝grace period |
| 隔離區 | Quarantine | 掃毒＝virus scan；攔截＝blocked |
| 研報管理 | Reports | 隱藏／恢復＝hide／restore |

## 維運

| 中文 | English | 備註 |
|---|---|---|
| 維運 | Operations | 維運代理＝ops agent |
| 管線 | Pipeline | 批次＝batch；新鮮度＝freshness；回填＝backfill |
| 服務 | Services | 核心／重要／輔助＝Core／Important／Supporting |
| 依賴圖 | Dependencies（圖＝dependency graph） | 上游／下游＝upstream／downstream；根因＝root cause |
| 來源主機 | Source hosts | 信標＝beacon |
| 事件 | Incidents | 進行中／已恢復／結束不明＝Open／Recovered／End unknown |
| 排程工作 | Scheduled jobs | |
| 資料健康 | Data health | R2 對帳＝R2 reconciliation |
| 診斷 | Diagnostics | |
| 正常／注意／異常 | OK／Warning／Error | 燈號用字；降級＝Degraded；故障＝Down |
