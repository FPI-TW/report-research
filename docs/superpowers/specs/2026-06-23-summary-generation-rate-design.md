# 監控頁「摘要產生速率」設計

日期：2026-06-23
狀態：已定案（brainstorming 完成、待實作）

## 背景與目標

`/monitor` 監控頁頂部已有「導入速率」（篇/分），用前端 `rate()` 以「開頁後 `db.reports` 的平均增速」估算。
但「摘要 SUMMARY」面板目前只顯示進度（已生成／總數／未生成＋進度條），**沒有任何速率**，看不出摘要產生有多快、還要多久跑完。

目標：在摘要面板加入**摘要產生速率（摘要/分）**與**預估剩餘時間（ETA）**。

## 範圍

- **純前端**，只動前端檔案。
- 後端 `/api/progress` 已回傳 `summary: {done, total, remaining, pct}`，**無須改 `web/server.py`**。
- 速率算法沿用現有導入速率的「開頁平均法」，與既有 UI 行為一致、穩定不抖動。

## 設計

### 1. 速率計算（擴充既有 `rate()`，monitor.html inline）

現有 `rate(reports, chunks)` 已用 `base` 快照算每分鐘增量。擴充為 `rate(reports, chunks, sumDone)`：

- `base` 多記一個 `sum`（開頁當下的 `summary.done`）。
- 回傳多一個 `spm`（摘要/分）＝ `(sumDone - base.sum) / dt * 60`。
- 與導入速率一致：開頁前 8 秒回傳 `null`（顯示「計算中…」），之後為開頁以來的穩定平均。
- `done` 單調遞增，故管線沒在跑時 `spm` 恰為 0、可據此判斷「未增長」。

### 2. ETA + 速率文字（純函式，可測）

新增純函式 `summaryRateText(remaining, spm)`，回傳摘要面板那行的**純文字**（無 HTML、無注入風險）：

| 條件 | 輸出 |
|------|------|
| `spm == null`（暖機中） | `速率 計算中…` |
| `spm < 0.05`（顯示為 0.0、視同未增長） | `速率 0.0 摘要/分`（不給 ETA） |
| `remaining <= 0`（已跑完） | `速率 X.X 摘要/分 · 已完成` |
| 其餘 | `速率 X.X 摘要/分 · 預估剩餘 ~N 分`／`~H.h 時` |

ETA 格式：`mins = remaining / spm`；`mins < 90` → `~N 分`（四捨五入），否則 `~H.h 時`。

### 3. 顯示

在「摘要 SUMMARY」面板進度條下方新增一行（新 `.psub` class）：

```
摘要 SUMMARY
  123            ／ 200   未生成 77
  [========>           ]   ← 既有進度條保留
  速率 4.2 摘要/分 · 預估剩餘 ~18 分   ← 新增
```

既有 done/total/未生成/進度條全部不動。

### 4. 檔案結構

- `web/static/app/eta.js`：匯出純函式 `summaryRateText`（含檔頭說明），無外部 import，可在瀏覽器外獨立驗證。
- `web/static/app/eta.test.mjs`：零工具鏈單元測試（`node web/static/app/eta.test.mjs`），對齊 `markdown.test.mjs` 風格。
- `web/static/monitor.html`：`import { summaryRateText }`；擴充 `rate()` 計 `spm`；新增 `.psub` 樣式與 `#sum-rate` 元素並於 `tick()` 更新。

## 測試

`eta.test.mjs` 涵蓋：暖機 `null`、零速率、低於門檻（0.0 不給 ETA）、一般值（18 分）、`remaining=0`（已完成）、大值換「時」、`<90` 分邊界。
`rate()` 維持 inline（與既有導入速率同模式、同樣不另測）。
回歸：Python 測試套件不受影響（純前端改動）。

## 不做（YAGNI）

- 不改後端、不新增 API 欄位（前端已有足夠資料）。
- 不做歷史曲線/圖表，只給目前速率與 ETA。
- 不另為導入速率 `rate()` 補測（維持現狀一致）。
