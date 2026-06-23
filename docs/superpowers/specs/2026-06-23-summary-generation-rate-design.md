# 監控頁面板速率顯示設計（摘要＋標註）

日期：2026-06-23
狀態：已實作（摘要先做，標註後續比照同模式擴充）

## 背景與目標

`/monitor` 監控頁頂部已有「導入速率」（篇/分），用前端 `rate()` 以「開頁後 `db.reports` 的平均增速」估算。
但「摘要 SUMMARY」與「標註 TAGGING」面板原本只顯示進度（done／total／剩餘＋進度條），**沒有任何速率**，看不出產生／標註有多快、還要多久跑完。

目標：在摘要與標註面板各加入**產生／標註速率（摘要/分、標註/分）**與**預估剩餘時間（ETA）**。

## 範圍

- **純前端**，只動前端檔案。
- 後端 `/api/progress` 已回傳 `summary: {done,total,remaining,pct}` 與 `tagging: {done,total,fail,pct}`（`fail`＝未標＝剩餘），**無須改 `web/server.py`**。
- 速率算法沿用現有導入速率的「開頁平均法」，與既有 UI 行為一致、穩定不抖動。

## 設計

### 1. 速率計算（擴充既有 `rate()`，monitor.html inline）

現有 `rate(reports, chunks)` 已用 `base` 快照算每分鐘增量。擴充為 `rate(reports, chunks, sumDone, tagDone)`：

- `base` 多記 `sum`（開頁當下 `summary.done`）與 `tag`（開頁當下 `tagging.done`）。
- 回傳多 `spm`（摘要/分）＝ `(sumDone - base.sum) / dt * 60`、`tpm`（標註/分）＝ `(tagDone - base.tag) / dt * 60`。
- 與導入速率一致：開頁前 8 秒回傳 `null`（顯示「計算中…」），之後為開頁以來的穩定平均。
- `done` 單調遞增，故管線沒在跑時 `spm`／`tpm` 恰為 0、可據此判斷「未增長」。

### 2. ETA + 速率文字（純函式，可測，摘要／標註共用）

純函式 `rateText(remaining, rate, unit)`，回傳面板那行的**純文字**（無 HTML、無注入風險）。`unit` 由呼叫端帶入（`"摘要"`／`"標註"`）：

| 條件 | 輸出 |
|------|------|
| `rate == null`（暖機中） | `速率 計算中…` |
| `rate < 0.05`（顯示為 0.0、視同未增長） | `速率 0.0 {unit}/分`（不給 ETA） |
| `remaining <= 0`（已跑完） | `速率 X.X {unit}/分 · 已完成` |
| 其餘 | `速率 X.X {unit}/分 · 預估剩餘 ~N 分`／`~H.h 時` |

ETA 格式：`mins = remaining / rate`；`mins < 90` → `~N 分`（四捨五入），否則 `~H.h 時`。

### 3. 顯示

在「摘要 SUMMARY」與「標註 TAGGING」面板進度條下方各新增一行（新 `.psub` class）：

```
摘要 SUMMARY                          標註 TAGGING
  123        ／ 200  未生成 77          1,200      ／ 3,600  未標 2,400
  [====>        ] ← 既有進度條保留        [==>          ] ← 既有進度條保留
  速率 4.2 摘要/分 · 預估剩餘 ~18 分      速率 5.0 標註/分 · 預估剩餘 ~8.0 時
```

摘要 remaining 用 `summary.remaining`；標註 remaining 用 `tagging.fail`（＝未標）。既有 done/total/剩餘/進度條全部不動。

### 4. 檔案結構

- `web/static/app/eta.js`：匯出純函式 `rateText`（含檔頭說明），無外部 import，可在瀏覽器外獨立驗證。
- `web/static/app/eta.test.mjs`：零工具鏈單元測試（`node web/static/app/eta.test.mjs`），對齊 `markdown.test.mjs` 風格；涵蓋摘要／標註兩種 unit。
- `web/static/monitor.html`：`import { rateText }`；擴充 `rate()` 計 `spm`／`tpm`；新增 `.psub` 樣式與 `#sum-rate`／`#tag-rate` 元素並於 `tick()` 更新。

## 測試

`eta.test.mjs` 涵蓋：暖機 `null`、零速率、低於門檻（0.0 不給 ETA）、一般值（摘要 18 分、標註 12 分）、`remaining=0`（已完成）、大值換「時」、`<90`／`=90` 分邊界（9 案）。
`rate()` 維持 inline（與既有導入速率同模式、同樣不另測）。
回歸：Python 測試套件不受影響（純前端改動）；瀏覽器 harness 實渲染摘要＋標註各 5 情境確認。

## 不做（YAGNI）

- 不改後端、不新增 API 欄位（前端已有足夠資料）。
- 不做歷史曲線/圖表，只給目前速率與 ETA。
- 不另為導入速率 `rate()` 補測（維持現狀一致）。
