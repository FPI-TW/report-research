# AWS 部署操作手冊與網頁簡報中心設計規格

日期：2026-08-14
狀態：已核准
核准日期：2026-08-14
交付形式：單一離線 HTML 檔案

## 1. 目標

建立一個可直接雙擊開啟的獨立 HTML 網頁，同時支援兩個用途：

1. 依照可執行 Runbook 完成 Report Mark 的 AWS 部署，記錄每一步完成狀態。
2. 以網頁簡報模式完整展示既有兩份管理簡報，供向上報告與說明決策。

網頁不依賴後端、npm、CDN 或網路服務。除了開啟 AWS Console 與官方文件連結外，所有閱讀、搜尋、簡報播放、勾選與進度保存功能都必須可在 `file://` 模式運作。

## 2. 使用者與使用情境

### 2.1 部署執行者

- 從全新 AWS 帳號開始建立環境。
- 以 Terraform 為主要部署方式，AWS Console 作為查驗與排錯入口。
- 需要可複製指令、完成條件、常見錯誤與回復步驟。
- 需要在關閉 HTML 後保留完成進度與個人備註。

### 2.2 管理報告對象

- 需要理解改善前後差異、AWS 目標架構、成本、效益、風險與部署時程。
- 需要依照簡報頁序逐頁報告，而不是閱讀濃縮儀表板。
- 需要全螢幕、鍵盤換頁、頁碼、縮圖與兩份簡報切換功能。

## 3. 明確不做的事項

- 不整合進現有 React 前端或 FastAPI 路由。
- 不建立使用者帳號、後端資料庫或雲端同步。
- 不在網頁中執行 Terraform、AWS CLI、SQL 或任何破壞性指令。
- 不保存 AWS Access Key、Secret、Token、密碼、Claude 憑證或真實 `.env` 內容。
- 不使用 AI 聊天、AI 推薦語、生成式摘要或宣傳式文案。
- 不把簡報壓縮成摘要儀表板，也不只嵌入簡報截圖。
- 不承諾 AWS、Anthropic 或 SageMaker 的供應商 SLA。

## 4. 最終檔案

主要交付檔案：

`AWS部署操作手冊_2026-08.html`

檔案包含：

- 語意化 HTML。
- 內嵌 CSS。
- 內嵌原生 JavaScript。
- 內嵌 SVG 圖示。
- 結構化部署步驟資料。
- 兩份簡報共 27 頁的結構化內容。

## 5. 全站資訊架構

網頁提供兩個主要入口：

### 5.1 部署 Runbook

呈現部署總進度、目前階段、下一個步驟、預算上限與八個部署階段。點入步驟後顯示完整操作內容。

### 5.2 簡報中心

提供兩份簡報的網頁播放模式：

1. `AWS上雲架構與完整成本評估_正式版_2026-08-10.pptx`，共 16 頁。
2. `問答推論成本效益評估_GPU明細版_2026-08-10.pptx`，共 11 頁。

兩個入口共用一致的 macOS 簡約玻璃磨砂視覺系統，但部署頁以操作效率為主，簡報頁以 16:9 報告畫布為主。

## 6. 部署 Runbook 架構

### 6.1 階段一：開始前準備

- AWS 帳號與 root account 保護。
- MFA、IAM Identity Center 或部署角色。
- AWS Budget、付款與成本通知。
- 網域與 DNS 現況。
- Terraform、AWS CLI、Docker、Git 版本檢查。
- 本機憑證與 AWS account／Region 查驗。

### 6.2 階段二：AWS 基礎與安全

- Terraform remote state、版本控管、KMS 加密與 state lock。
- IAM 最小權限、CloudTrail、Secrets Manager、KMS。
- ap-east-2 台北區雙 AZ VPC。
- public／private subnet、route table、NAT 與必要 VPC endpoints。
- 安全群組、網路出口與外部 HTTPS egress 控制。

### 6.3 階段三：資料與向量庫

- Amazon RDS PostgreSQL 與 pgvector。
- 建議起始配置 4 vCPU、16 GB RAM、100 GB 儲存空間。
- Multi-AZ、PITR、備份、還原與連線測試。
- 既有約 25 GB 資料與 PDF 搬遷。
- 576,305 vectors 與 HNSW 索引驗證。
- `m=16`、`ef_construction=64` 起步；recall 不足時測試 128。
- 大量重建時調整 `maintenance_work_mem`，完成後調回。
- PgBouncer 或 RDS Proxy 連線治理。

### 6.4 階段四：Web／API 服務

- ECR repository 與容器建置。
- ECS Fargate task definition、service、private subnet。
- ALB、target group、health check、ACM 與 Route 53。
- 環境變數、Secrets、應用啟動與 SPA 驗證。
- Auto Scaling、rolling deployment 與前一版回復方式。

### 6.5 階段五：文章導入路徑

- S3 PDF 來源與物件規則。
- SQS、DLQ、EventBridge 排程與重試政策。
- Private EC2 Claude CLI Worker。
- Claude Max 20x 登入、健康檢查、併發 1–2 與每日額度。
- Python 固定 chunking：600 字、80 字重疊。
- BGE-M3 Embedding Worker 與批次寫回 RDS。
- 文章狀態：排隊中、處理中、失敗、已完成。

### 6.6 階段六：問答推論路徑

- 正確順序：RDS hybrid retrieval → reranker → Claude API → 回傳答案。
- Claude API Secrets、timeout、重試、熔斷與用量紀錄。
- SageMaker Async reranker 的部署與 scale-to-zero。
- ECS CPU fused ranking fallback。
- Async 未通過延遲 gate 時，評估 SageMaker Real-time。

### 6.7 階段七：監控與營運

- CloudWatch、X-Ray／OpenTelemetry 與 trace-id。
- Claude API p95／p99、429、timeout。
- RDS CPU、FreeableMemory、DatabaseConnections、query p95。
- Queue oldest-message age、DLQ。
- CLI 登入與 Max 20x 額度健康檢查。
- 外部 API egress 與密鑰輪替。
- 每項告警對應預防控制與 Runbook。

### 6.8 階段八：壓測、灰度切換與回復

- 使用不同 context 長度與接近正式流量的測試情境。
- 量測端到端 p50／p95／p99。
- 拆解 RDS 檢索、rerank 排隊與推論、Claude 首 token 與完整生成時間。
- SageMaker reranker 暫定 gate：端到端 p95 < 3.5 秒。
- Async 過關時使用 Async＋CPU fallback；未過關時評估 Real-time＋CPU fallback。
- 答案品質、fallback 命中率、資料一致性、DNS 切換與回復演練。

## 7. Runbook 步驟資料模型

每個步驟具有固定欄位：

- `id`：穩定且唯一的步驟識別碼。
- `phaseId`：所屬部署階段。
- `title`：直接描述操作，不使用宣傳語。
- `purpose`：建立的資源與用途。
- `prerequisites`：前置條件。
- `files`：應建立或修改的 Terraform、設定或容器檔案。
- `commands`：可複製指令與用途說明。
- `consoleChecks`：AWS Console 查驗位置。
- `acceptance`：完成條件。
- `failures`：常見錯誤、原因與處理方式。
- `rollback`：回復方式。
- `costImpact`：開始產生的固定或變動成本。
- `risk`：一般、高風險或破壞性提醒。

破壞性操作只能顯示說明與驗證步驟，不提供無確認的一鍵執行行為。

## 8. Runbook 互動

- 勾選步驟並即時計算整體與各階段進度。
- 保存個人備註。
- 保存展開章節與上次瀏覽位置。
- 搜尋資源、指令與錯誤訊息。
- 篩選全部、未完成、已完成與高風險步驟。
- 複製程式碼；Clipboard API 不可用時退回文字選取。
- 匯出與匯入版本化進度 JSON。
- 重設進度前二次確認。

## 9. localStorage 設計

儲存內容：

- schema version。
- 完成步驟 ID。
- 步驟備註。
- 上次頁面、階段與捲動位置。
- 簡報中心上次選擇的簡報與頁碼。

不得儲存：

- AWS 憑證。
- Claude／Anthropic 憑證。
- Terraform state。
- `.env` 內容。
- 使用者複製的指令輸出。

遇到未知版本或損壞資料時，先保留原始字串作為本機備份，再載入安全預設狀態。匯入 JSON 時驗證 schema version、資料型別與步驟 ID；不接受 HTML 或 JavaScript 字串作為可執行內容。

## 10. 簡報中心設計

### 10.1 播放介面

- 左側顯示目前簡報、完整頁次、縮圖與頁面標題。
- 中央顯示可隨視窗縮放的 16:9 原生 HTML 畫布。
- 底部提供上一頁、下一頁、頁碼與全螢幕。
- 支援鍵盤左右方向鍵、Home、End、Escape 與 `F` 全螢幕。
- 可在兩份簡報間切換，並記住各自最後頁碼。
- 行動版隱藏縮圖側欄，改用頁面選單與觸控換頁。

### 10.2 內容忠實度

兩份簡報共 27 頁都必須逐頁重建，保留：

- 原頁序。
- 原標題與可見文字。
- 管理摘要與決策請求。
- 表格的所有列、欄與數值。
- 圖表數據、尺度與圖例。
- 改善前後比較。
- 成本區間、假設與排除項目。
- 架構與資料流順序。
- 工程日、部署時程與驗收門檻。
- 每頁來源與必要備註。

不要求複製 PowerPoint 的每個像素；視覺改用已核准的低漸層、低立體 macOS 玻璃磨砂系統。但內容、數字、關係、頁序與敘事不可濃縮、刪除或改寫成摘要儀表板。

### 10.3 AWS 上雲成本正式版範圍

完整重建以下 16 頁：

1. AWS 上雲架構與完整成本。
2. AWS 混合架構管理摘要。
3. 既有資料量與搬遷基準。
4. 即時問答與批次導入雙路徑架構。
5. Claude CLI、Python chunking 與 BGE-M3 的責任分工。
6. 每日與每月導入 Token。
7. Claude Max 20x 訂閱成本與額度風險。
8. Claude API 與 SageMaker GPU 問答成本。
9. AWS 基礎設施成本。
10. AWS 上雲完整 TCO。
11. 一次性遷移工程日與預算。
12. 三階段切換時程。
13. 資料寫入與 HNSW 維護策略。
14. 六項 Alarm 與 Runbook。
15. SageMaker Async／Real-time／CPU fallback 決策。
16. 上雲決策請求。

### 10.4 GPU 明細版範圍

完整重建以下 11 頁：

1. 問答推論成本評估。
2. C2 管理摘要。
3. CLI＋CPU 與 API＋Flex GPU 改善前後。
4. p50／p95 改善與等待時間。
5. Claude API 單價與每題成本。
6. Flex／Active GPU 成本。
7. 題量增加時的 API 成本敏感度。
8. C1／C2／C3 方案比較。
9. 7–13 工程日投入。
10. 三道財務控制。
11. C2 分階段試行決策。

### 10.5 來源與時效

- 每頁顯示簡短來源標記與資料日期。
- 詳細來源可在頁面內的「來源」控制展開。
- 「現況實測」「規劃估算」「導入目標」「內部驗收 gate」使用不同文字標籤，避免誤解。
- RunPod Flex／Active 僅作 GPU 明細版的替代方案比較，不與 AWS 主方案成本重複加總。

## 11. 視覺設計

### 11.1 方向

- macOS 簡約玻璃磨砂。
- 低彩度淺灰藍背景。
- 單一藍色為主要操作色，綠色只代表完成。
- 玻璃效果用於內容分層，不作大量裝飾。
- 陰影薄、圓角克制、避免厚重立體感。
- 不使用大面積多色漸層、強烈光暈或重複卡片網格。

### 11.2 文字

- 優先使用系統字型，包含 SF Pro、PingFang TC 與 Microsoft JhengHei fallback。
- 所有可見文案直接描述操作、數據、來源與結論。
- 不加入 AI 文案、行銷式標語、虛構效益或未經來源支持的結論。

### 11.3 美化標準

- 透過字體層級、對齊、留白、表格節奏與圖表比例呈現專業感。
- Runbook 首屏清楚顯示目前步驟，不堆疊所有細節。
- 簡報畫布在常見筆電尺寸維持完整 16:9 可讀性。
- 表格、圖表、架構與比較頁使用各自適合的版型，不用單一模板重複套用所有頁面。

## 12. 響應式與無障礙

- 桌面版使用側欄與主內容區。
- 行動版使用頂部導覽與可展開階段／頁次選單。
- 控制項具有鍵盤焦點、可辨識標籤與足夠點擊區域。
- 顏色不是唯一狀態提示。
- 支援 `prefers-reduced-motion`。
- 程式碼區塊允許橫向捲動，不裁切指令。
- 表格在小螢幕使用可捲動容器或重新排列，不縮到無法閱讀。

## 13. 錯誤處理

- localStorage 不可用時顯示非阻斷通知，功能退化為單次工作階段。
- Clipboard API 失敗時選取文字並提示手動複製。
- 匯入進度格式不正確時拒絕載入，不覆蓋現有資料。
- 全螢幕 API 不可用時保留一般播放模式。
- 外部連結無網路時不影響本機內容。
- 找不到步驟或簡報頁面 ID 時回到安全的入口頁。

## 14. 測試與驗收

### 14.1 功能驗收

- 勾選、取消與進度計算正確。
- 重新開啟 HTML 後進度、備註與上次位置正確。
- 搜尋與四種篩選正確。
- 複製、匯出、匯入與重設行為正確。
- 兩份簡報切換、縮圖、方向鍵、Home／End、全螢幕與頁碼正確。
- 27 頁皆可到達且不重複或遺漏。

### 14.2 內容驗收

- 八個部署階段都有前置條件、指令、Console 查驗、完成條件、錯誤處理、回復與成本影響。
- 兩份簡報逐頁與來源 PPTX 對照。
- 表格數字、圖表數據、成本加總、工程日與 Token 區間一致。
- RAG 順序維持 RDS retrieval → reranker → Claude generation。
- 規劃值與實測值標示清楚。

### 14.3 視覺驗收

- 桌面與手機尺寸均無內容裁切或水平頁面溢出。
- Runbook 與簡報中心視覺一致，但資訊密度符合各自用途。
- 逐頁檢查表格、圖表、程式碼、架構與來源標記。
- 玻璃、陰影與漸層符合低彩度、低立體限制。

### 14.4 安全驗收

- 掃描 HTML，不包含真實 AWS、Anthropic、Claude 或資料庫密鑰。
- 不包含 `.env` 內容。
- 不包含可被匯入 JSON 執行的 HTML／JavaScript。
- 外部連結使用明確可見目的地與安全屬性。

## 15. 完成定義

符合下列條件才算完成：

1. 單一 HTML 可在 Chrome／Edge 以 `file://` 開啟。
2. Runbook 八階段完整且可操作。
3. localStorage 進度、備註與匯出／匯入可用。
4. 兩份簡報共 27 頁完整重建並可全螢幕播放。
5. 內容與兩份來源簡報核對無遺漏。
6. 桌面與行動版通過視覺與功能驗收。
7. 無真實密鑰、敏感資料、AI 文案或未標示的推估數字。
