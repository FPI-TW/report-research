# 設計交付存檔

歷次介面改版的設計稿與交付物存檔。**這裡的檔案不參與建置、不被應用程式引用**，只作為設計決策的歷史依據。

| 目錄 / 檔案 | 內容 | 對應實作 |
|---|---|---|
| `search-redesign/` | 檢索頁改版交付（HTML 原型 + CSS） | PR #84 檢索頁 Bento 改版 |
| `frontend-redesign/` | 前端整站重設計（Claude Design 互動原型、現況復刻、設計提案、改版筆記、截圖） | PR #60 墨青×鎏金、PR #84 |
| `廷豐智能研報.dc.html` | 品牌／配色設計稿 | 品牌改版（藍 → 金 #AE7415） |
| `廷豐研報改善規劃.pptx` | 改善規劃簡報 | — |

## 為什麼 `frontend-redesign/` 沒有元件原始碼

原始交付包內含一份 `frontend/src/` 子樹（14 個 `.tsx` + 32 個 `.css`），是當時現行前端元件的**複製版**（`CitePreview.tsx`、`ReportDetailModal.tsx`、`askMarkdown.tsx` 等皆有同名副本）。

收進版控會讓 `grep` 元件名同時命中真檔與陳舊副本，日後可能有人改到錯的檔案，而副本只會持續腐爛。因此**只保留設計產物，排除那 46 個複本**。設計意圖看 `frontend/REDESIGN_NOTES.md` 與三份 `.dc.html` 原型即可；真正的元件一律以 `frontend/src/` 為準。
