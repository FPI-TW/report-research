# 標籤顯示具體標的(個股代碼清單 + 期貨商品)— 設計

- 日期:2026-06-09
- 範圍:report-mark 研報檢索
- 目標:結果卡片的「個股 / 期貨」標籤,除了布林旗標外,還要顯示**是哪個標的**——個股顯示股票代碼清單,期貨顯示期貨商品(小詞表)。

## 1. 背景與現況

- 現有標籤維度:`market`、`is_research`、`confidence`、`instrument_types[]`、`relates_stock`、`relates_futures`。
- `relates_stock` / `relates_futures` 只是布林,不知道「哪一檔/哪個期貨」。
- 既有 `stock_code` / `company_name`(單值,`filename.py` 從檔名解析)涵蓋 45/72 篇個股報告,但:
  - 不在 `/api/search` 回傳、前端沒顯示;
  - 週報/產業/策略(20 篇 `relates_stock` 但無 code)沒有標的;
  - 期貨**完全無**標的資料;
  - 有檔名誤判(`0209 債券雙週報` → 假 `stock_code=0209`)。
- 2026-06-09 已完成全量稽核,現有 `market/instrument_types/relates_*` 全部正確(見 `[[tag-audit-2026-06-09]]`)。

## 2. 設計決策(已與使用者確認)

1. **augment 不覆寫**:不重判市場與既有欄位,只讓 Claude 讀內文「補抽」兩個新欄位,合併進既有 tag JSON。
2. **個股顯示代碼即可**:`stock_targets` 存 4 碼代碼(不存名稱),與 `instrument_types` 同為 `text[]`。
3. **期貨用小詞表**(見 §4)。
4. **顯示上限**:卡片內嵌最多 **3** 檔,超過顯示「首檔 等 N 檔」;儲存最多 **8** 檔(依重要性排序)。完整清單放 `title` tooltip。
5. **只做顯示,不做篩選**(YAGNI;GIN index 先建好,日後要篩再加 API 參數)。

## 3. 資料模型(`db/schema.sql`,冪等 ALTER)

```sql
ALTER TABLE research.research_report
  ADD COLUMN IF NOT EXISTS stock_targets   text[];   -- 個股代碼清單 ["2330","2303"]
ALTER TABLE research.research_report
  ADD COLUMN IF NOT EXISTS futures_targets text[];   -- 期貨商品 ["台指期"]

CREATE INDEX IF NOT EXISTS idx_rr_stock_targets
  ON research.research_report USING gin (stock_targets);
CREATE INDEX IF NOT EXISTS idx_rr_futures_targets
  ON research.research_report USING gin (futures_targets);
```

- 兩欄皆 `text[]`,nullable(空清單以 `NULL` 或 `{}` 表示,前端兩者都當「無」)。

## 4. 抽取規則(`app/services/tagging.py` + `workflows/tag_reports.workflow.js`)

### MarketTag 擴充
```python
stock_targets: Optional[list[str]] = None      # 4 碼代碼
futures_targets: Optional[list[str]] = None     # 小詞表
```

### 期貨小詞表(`FUTURES_TARGETS`)
`台指期`、`小型台指`、`電子期`、`金融期`、`個股期貨`、`其他`
(詞表外一律不輸出;可日後擴充。)

### 標註指令新增兩段
- `stock_targets`:報告**聚焦/評等/主要討論**的個股,輸出 4 碼代碼清單(字串),依重要性排序,**上限 8 檔**。
  - 個股報告 → 1 檔(該標的)。
  - 週報/策略/產業 → 列出文中**重點討論**的個股代碼(非順帶提及),控制在代表性的幾檔內。
  - 無明確個股(純總經/匯率)→ `[]`。
  - 只輸出能確定 4 碼代碼者;只有名稱無法確定代碼則略過該檔。
- `futures_targets`:報告涉及的期貨商品,從上列小詞表選 0 到多個。
  - 台指期盤後報 → `["台指期"]`(若也談電子/金融期則加上)。
  - `relates_futures=true` 但無具體期貨商品(總經/債券對部位有參考價值)→ `[]`。

### parse_tags() 防呆
- `stock_targets`:過濾為「正好 4 碼數字」的字串、去重保序、截斷到 8 檔;其餘丟棄(擋掉 `0209` 這類假代碼——日期/頁碼)。
- `futures_targets`:僅保留小詞表內的值、去重保序。

## 5. 「多檔」顯示規則(`web/static/index.html`)

`subj` 徽章改為帶標的:
```
個股報告:    [個股 · 2330]
2~3 檔:      [個股 · 2330、2303]
>3 檔:       [個股 · 2330 等 6 檔]        ← title tooltip 顯示完整清單
台指期:      [期貨 · 台指期]
期貨多項:    [期貨 · 台指期、電子期]
無具體標的:  [個股]  /  [期貨]            (維持通用,如總經週報)
```
- 規則:`relates_stock` 為 true 才出個股徽章;有 `stock_targets` 就接「· 代碼」,內嵌最多 3 個,>3 顯示「首檔 等 N 檔」。期貨同理。
- 維持上一個需求:**徽章不放 emoji**(見 `[[ui-no-emoji-on-tags]]`)。

## 6. 串接(threading)

| 檔案 | 變更 |
|------|------|
| `db/schema.sql` | 加兩欄 + 兩個 GIN index(§3) |
| `app/services/tagging.py` | `MarketTag` 兩欄、`FUTURES_TARGETS` 詞表、`TAG_INSTRUCTION` 兩段、`parse_tags()` 防呆、normalize 函式 |
| `workflows/tag_reports.workflow.js` | `TAG_RULES` 同步加兩段與輸出格式 |
| `app/services/store.py` | `ReportRow` 兩欄;INSERT 加 `stock_targets`、`futures_targets`(`CAST(:x AS text[])`) |
| `scripts/run_ingest.py` | `ReportRow(... stock_targets=tag.stock_targets, futures_targets=tag.futures_targets)` |
| `web/server.py` | `ReportResult` 兩欄;`search_chunks_meta` / SQL select 帶回兩欄並 hydrate |
| `web/static/index.html` | `subj` 徽章渲染依 §5 |

(篩選 API 參數**不在範圍**。)

## 7. 執行流程

1. 套 schema:`docker exec -i report-mark-postgres psql -U postgres -d research < db/schema.sql`
2. 改上述 6 個程式檔。
3. **補抽標的**:派平行 agent 讀 74 篇內文,只輸出 `stock_targets`/`futures_targets`,**合併**進既有 `data/tags/<hash>.json`(保留原欄位)。先備份 `data/tags`。
4. 重灌:`uv run python scripts/run_ingest.py --force`。
5. 驗證:`/api/stats` 不變;`/api/search` 回傳新欄位;頁面卡片個股顯示代碼、台指期盤後報顯示「期貨 · 台指期」、總經週報維持通用「個股/期貨」。

## 8. 測試 / 驗收

- 個股報告(如 `台積電(2330)`)→ 徽章顯示「個股 · 2330」。
- `1102 TT`(無中文名)→ 顯示「個股 · 1102」。
- 週報多檔 → 「個股 · XXXX 等 N 檔」,tooltip 全清單,且儲存 ≤8 檔。
- `【元大期貨】台指期盤後報` → 「期貨 · 台指期」。
- 總經週報(無具體個股)→ 維持通用「個股」「期貨」。
- `0209 債券雙週報` → `stock_targets` 不含 `0209`(防呆生效)。
- 既有 `market/instrument_types/relates_*` 重灌後不變(augment 驗證)。

## 9. 風險 / 備註

- 重抽會再跑一輪 Claude 標註(成本同稽核);採 augment 合併,避免動到已驗證欄位。
- 代碼抽取依賴內文有明確 4 碼代碼;只有名稱無代碼者略過(可接受,個股報告檔名本就有代碼)。
- 環境目前非 git repo,spec 不入版控(僅存檔)。
