# 設計交付存檔

歷次介面改版的設計稿與交付物存檔。**這裡的檔案不參與建置、不被應用程式引用**，只作為設計決策的歷史依據。

| 目錄 / 檔案 | 內容 | 對應實作 |
|---|---|---|
| `search-redesign/` | 檢索頁改版交付（HTML 原型 + CSS） | PR #84 檢索頁 Bento 改版 |
| `frontend-redesign/` | 前端整站重設計（Claude Design 互動原型、現況復刻、設計提案、改版筆記、截圖） | PR #60 墨青×鎏金、PR #84 |
| `廷豐智能研報.dc.html` | 品牌／配色設計稿 | 品牌改版（藍 → 金 #AE7415） |
| `廷豐研報改善規劃.pptx` | 改善規劃簡報 | — |

## 為什麼 `frontend-redesign/` 沒有元件原始碼

原始交付包內含一份 `frontend/` 子樹（14 個 `.tsx` + 32 個 `.css` + 一份 `index.html`），內容與當時現行的前端高度重疊：`index.html` 與 repo 的 `frontend/index.html` **位元組完全相同**，元件則多數同名（`CitePreview.tsx`、`ReportDetailModal.tsx`、`askMarkdown.tsx`…），其中約 30 個是改過樣式的設計變體、14 個一字未改。

收進版控會讓 `grep` 元件名同時命中真檔與陳舊副本，日後可能有人改到錯的檔案，而副本只會持續腐爛；那份 `index.html` 還會指向被排除的 `/src/main.tsx`，收進來即是斷鏈。因此**這 47 個檔案一律不進版控**。

設計意圖看 `REDESIGN_NOTES.md` 與三份 `.dc.html` 原型即可；真正的元件一律以 `frontend/src/` 為準。**設計變體的完整原始包另存於 repo 外**（見下）。

## 存檔與外部依賴

- 完整原始交付包（含上述 47 個檔案）存放在 **`~/design-archive/report-mark/廷豐智能研報前端重設計/`**，不在版控內。需要比對設計變體與現行實作時去那裡找。
- `frontend-redesign/support.js` 是 Claude Design 的 runtime **建置產物**（檔頭自述 `GENERATED from dc-runtime/src/*.ts`，無授權標頭），非本專案原始碼。三份 `.dc.html` 少了它就無法運作，故一併存檔。
- 三份 `.dc.html` 開啟時會從 unpkg 載入 React 與 Babel standalone，也就是說**雙擊開啟會執行遠端程式碼**。當作離線設計存檔閱讀即可。
