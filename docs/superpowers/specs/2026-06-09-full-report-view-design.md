# 查看完整報告(頁內全文 modal + 開啟原始檔)— 設計

- 日期:2026-06-09
- 範圍:report-mark 研報檢索
- 目標:結果卡片目前只顯示搜尋命中的節錄片段;新增「查看完整報告」,可在頁內讀全文,並能開啟原始檔(PDF 看圖表)。

## 1. 背景

- 卡片只渲染 `passages`(命中 chunk),看不到整篇。
- chunk **有重疊**(`chunk.py`:CHUNK_SIZE=600 / CHUNK_OVERLAP=80,且相鄰塊接上前一塊尾端),故直接串接 chunk 會重複文字 → 不可作為全文來源。
- 乾淨全文在 `data/extracted/sample.jsonl` 的 `text` 欄;原始 PDF/DOCX 仍在 `研報自動匯入/`,DB `file_path` 指得到。

## 2. 設計決策(已與使用者確認)

- 兩者都要:**頁內全文 modal**(所有檔都能看)+ modal 內「**開啟原始檔**」連結(PDF 內嵌看圖表;.docx 走下載)。
- 全文來源:**存進 DB `full_text` 欄**(避免 runtime 依賴 jsonl、也避免 chunk 重疊問題);以一次性回填填入,**不重新嵌入**。

## 3. 全文儲存

- `db/schema.sql`:`ALTER TABLE research.research_report ADD COLUMN IF NOT EXISTS full_text text;`(並加進 CREATE TABLE 區塊)。
- `app/services/store.py`:`ReportRow` 加 `full_text: Optional[str]`;INSERT 欄位 + value 加 `full_text`。
- `scripts/run_ingest.py`:`ReportRow(... full_text=rec.get("text"))`(未來 ingest 自動帶入)。
- `scripts/backfill_full_text.py`(一次性):讀 jsonl,依 `file_hash` `UPDATE research.research_report SET full_text=:t WHERE file_hash=:h`;不碰 chunk/embedding。

## 4. 後端 endpoint(`web/server.py`)

- `ReportResult` 加 `report_id: str`(讓前端能呼叫下列 endpoint;由 grouping 的 rid 帶入)。
- `GET /api/report/{report_id}/full`:
  - 查 `SELECT file_name, market, source, report_date, report_type, file_path, full_text FROM research.research_report WHERE id=:id`。
  - 回 `{file_name, market, source(display), report_date, report_type, full_text, has_file}`;`has_file` = `os.path.exists(file_path)`。
  - 查無 → 404。
- `GET /api/report/{report_id}/file`:
  - 由 id 查 `file_path`;不存在或檔案不在 → 404。
  - `FileResponse(file_path, media_type=..., filename=...)`;副檔名 `.pdf` → `application/pdf`(瀏覽器內嵌);其他 → 預設(下載)。
  - 路徑一律由 DB 依 id 取得,無使用者輸入 → 無路徑注入風險。

## 5. 前端(`web/static/index.html`)

- 卡片在片段區下方加「查看完整報告」鈕(`data-report-id` 帶 id)。
- 點擊 → `fetch('/api/report/<id>/full')` → 開 **modal**:
  - header:檔名 + 市場徽章 + 日期/券商/類型(沿用既有顯示函式);右上關閉鈕。
  - body:可捲動全文(`white-space: pre-wrap`,純文字)。
  - footer:`has_file` 為真時顯示「開啟原始檔」連結 → `/api/report/<id>/file`(`target=_blank`);否則隱藏。
- modal 關閉:背景遮罩點擊 / Esc / 關閉鈕。
- 新增 modal 與遮罩 CSS;徽章/按鈕**維持無 emoji**(見 ui-no-emoji-on-tags)。

## 6. 驗證 / 驗收

- backfill 後:DB 72 篇皆有非空 `full_text`。
- `/api/report/<id>/full` 回完整全文 + 正確 meta + `has_file`。
- `/api/report/<id>/file`:PDF 在新分頁內嵌開啟;.docx 觸發下載;不存在回 404。
- 頁面:點「查看完整報告」彈出 modal、全文可捲動、PDF 連結可開、Esc/背景可關。
- 既有搜尋/標籤顯示不受影響。

## 7. 備註

- 全文最長約 114k 字,modal 以捲動處理。
- 代碼搜尋/篩選功能本次不做(另案)。
