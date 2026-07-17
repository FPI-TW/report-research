# 觀點雷達改版 — 實作 spec / plan（2026-07-16）

視覺設計已於 Claude Design 定案（專案「廷豐研報 · 觀點雷達改版」）。本文件把定案設計移植回 React SPA，並為 picker 卡片格擴充後端。品牌沿用廷豐（墨青×鎏金、Noto Serif TC、`--tf-*`），後端 enum/契約前後端逐字鏡像不變。

## 目標

1. **overview**（`/app/radar` 已選標的）：移植簡約版版面 — 關鍵數字升為 hero、共識定盤（立場＋淨變動）降為「券商共識」區、四向觀點精簡、近期變化輕量引用、各券商表格。純前端（現有 `radarOverviewSchema` 已足）。
2. **picker**（未選標的）：改為**共識卡片格**，頂部走 **1a 編輯部報頭**。每張卡預覽該檔立場／迷你評等分佈／淨上下調／目標價中位＋修正。需**擴後端**讓清單端點多回傳每檔精簡共識。

## 非目標

- 不動 overview 的資料契約（欄位已齊）。
- 不動 `/api/instrument/{code}/radar`、`/api/instrument/{code}/radar/brokers/{broker}` 邏輯。
- 不重算訊號、不呼叫 LLM。
- 不做深色模式、不改 Modernist 專案。

## 後端 — 清單端點加「精簡共識」

檔案：`app/services/radar/{queries,compute,schemas}.py`、`web/server.py`、`tests/`。

### 資料流（避免 N+1）

1. `list_radar_instruments` 照舊取當頁 ≤50 列 `RadarInstrumentRow`。
2. **新增 `fetch_signals_for_instruments(session, keys)`**：一次 `WHERE (s.market, s.instrument_code) IN (...) AND s.extraction_status = ANY(:statuses)`，回傳當頁所有訊號（沿用 `SIGNAL_SELECT_COLUMNS` / `parse_signal_row`）。
3. Python 依 (market, code) 分組，對每檔跑**精簡共識**（見下），窗期預設 `90`、per-instrument 以各自 as_of 起算。
4. 掛到每個 `RadarInstrumentItem.consensus`。

估算：≤50 檔 × 數十列 = 單次查詢數千列上限、Python 算 50 份精簡共識，一次額外查詢、無往返風暴。加 `elapsed_ms` log。

### 精簡共識（重用既有函式）

新增 `compute.build_instrument_slim(signals, *, window="90")`，重用 `_by_broker` / `_consensus_set` / `_rating_consensus` / `_target_consensus`，只取：

- `stance`：中位評等 → 由五級分佈算加權中位（buy>overweight>neutral>underweight>sell），輸出 `RatingNorm` + 由前端對照顯示詞。**新增共用 helper `scale.median_rating(distribution)`**。
- `distribution`：五級 counts（迷你條）＋ `bullish/neutral/bearish`。
- `net_rating`：`upgrades / downgrades`（卡片「淨上調 N」= up−down）。
- `target`：primary group 的 `median / currency / revision_pct / revision_direction`。
- 無共識（window_empty / 尚未擷取）→ `consensus=None`，卡片走「資料擷取中」淡態。

### Schema（`schemas.py`，前端 zod 鏡像）

```py
class InstrumentStance(BaseModel):
    rating: RatingNorm                 # 中位評等
    bullish: int; neutral: int; bearish: int
    distribution: list[RatingBucketCount]
    net_rating: int                    # up - down
    upgrades: int; downgrades: int

class InstrumentTargetBrief(BaseModel):
    currency: str; median: float
    revision_pct: Optional[float] = None
    revision_direction: Direction = "none"

class InstrumentConsensus(BaseModel):
    window: Window
    stance: InstrumentStance
    target: Optional[InstrumentTargetBrief] = None

# RadarInstrumentItem 追加：
    consensus: Optional[InstrumentConsensus] = None
```

同步在 `RatingConsensus` 加 `median_rating: Optional[RatingNorm]`（讓 overview 的「中位立場」也用同一後端邏輯，不用前端重推）。

### 端點

`GET /api/radar/instruments`：回應加 `consensus`；query 加 `with_consensus: bool = True`（關掉可回舊行為，測試/降載用）。

## 前端 — schema/api 鏡像

`frontend/src/lib/radarSchemas.ts`：加 `instrumentConsensusSchema` 等，`radarInstrumentItemSchema` 追加 `consensus`；`ratingConsensusSchema` 加 `median_rating`。`radarApi.ts` 型別跟上。

## 前端 — overview 移植（features/radar）

逐元件對齊定案 HTML：

- **RadarHeader**：精簡為麵包屑＋窗期分段＋襯線標的名＋摘要列。移除頁面內重複「研報觀點變化雷達」標題與 info 泡泡（免責改置底 notes，已存在）。
- **關鍵數字 hero（新元件 `KeyFigures`）**：目標價／EPS／資料品質三欄同面板、44px 襯線值、方向標籤、資料品質細進度條。置於 header 下、最前。
- **ConsensusSnapshot → 共識定盤**：改為「券商共識」區的 tape（中位立場襯線鎏金詞＋五級分佈＋中位指針＋右側評等淨變動）。中位立場用 `rating.median_rating`。
- **ThesisCompass**：精簡為 名稱＋方向色標籤＋轉強/轉弱分佈條＋家數（移除 sample_summary 顯示）。
- **RecentChanges**：時間軸鎏金節點保留；引用改輕量斜體 inline＋右側「原始研報」連結。
- **BrokerList**：套用新表格樣式（主題化表頭、右對齊 tabular、方向標籤、窗外最新徽章）。**保留展開→ BrokerTimeline 下鑽功能**（靜態稿沒畫展開態，但這是既有真功能，不移除；展開列樣式跟著改）。→ 見「待確認 1」。
- CSS Modules 逐一改寫；keyframe 就地定義（沿用 [[css-modules-keyframe-localization]] 教訓）。

## 前端 — picker 重建為卡片格（1a）

`InstrumentPicker.tsx` 重寫 + 新增 `InstrumentCard`：

- **1a 編輯部報頭**：左「廷豐雷達 / 券商觀點一眼掌握」、右統計列（檔數／券商數／最新更新）、搜尋自成一帶、市場 chips＋顯示筆數。
- **卡片格**（`auto-fill minmax(300px,1fr)`）：名稱/代碼/市場徽章、立場襯線詞（多/中立/空 色）＋淨上下調標籤、迷你五級分佈條、目標價中位＋修正、頁尾 meta＋箭頭。
- 資料來自清單 API 的 `consensus`；`consensus=null` → 淡態卡（顯示家數/份數/最新，不放共識）。
- loading（骨架卡）／empty／error 狀態。
- 統計列數字（總檔數）用清單 API `total`；「家券商/最新更新」用頁面聚合或既有欄位。

## 測試

- 後端：`build_instrument_slim` 純函式（分佈/中位/淨變動/目標修正/無共識）；`fetch_signals_for_instruments` 批次；端點回傳 `consensus` 形狀；`median_rating` 邊界。
- 前端 vitest：`InstrumentCard`（多/空/淡態）、picker 版面、overview 重構後既有測試更新（RadarPage/DirectionTag 等）。
- 全綠後才開 PR。

## 流程 / rollout

- 從 `origin/main` 開分支 `feat/radar-redesign`（乾淨 PR，見 [[branch-off-origin-main-for-clean-pr]]）。
- 共用樹：只 stage 明確路徑，`git diff --staged --stat` 核對（[[shared-worktree-user-wip-hazard]]）。
- 後端先行、前端契約鏡像跟上，最後整支自審（overview 移植的 HTTP 端點縫是歷史地雷，見 [[report-qa-m3-qa-ux-2026-07-13]]）。
- 部署：重建 `frontend/dist` + `sudo systemctl restart report-mark-web.service`；後端 schema 無新表（`report_signal` 已存在），免 migration。

## 待確認

1. **各券商表格的展開下鑽**：定案靜態稿移除了展開欄，但 React 版有「展開→單券商歷程時間線」的真功能。建議**保留下鑽、只換視覺**；若你要純靜態表（放棄下鑽）請說。
2. **picker 卡片的淨變動窗期**：預設 `90` 天（對齊 overview 預設）。可改。
