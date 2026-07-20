# M9a — Typst 渲染引擎實作計畫

規格：`docs/superpowers/specs/2026-07-17-m9a-typst-render-design.md`
分支：`feat/m9a-typst-render`（base `origin/main` @ `16493ae`）

## 任務序列與相依

```
T1 依賴/設定 ──→ T2 中介模型 ──→ T3 模板契約 ──→ T4 ib-classic ──→ T5 接線/雙軌 ──→ T6 免責兩軌 ──→ T7 審查收尾
                                       └─────────→ T4 可與 T3 交錯（契約先凍結）
```

每個 task 結束時樹必須可 build、測試綠。

## T1 — 依賴與設定

- `uv add typst pypandoc-binary`（spike 已驗證：零手動 binary、零 systemd PATH）。
- `app/config.py`：`report_renderer: str`（`REPORT_RENDERER`，**預設 `typst`**，D3）。
  未知值 → 退回 `typst` 並 log（fail-safe，不讓 typo 變成靜默走舊路徑）。
- **DoD**：`uv run python -c "import typst, pypandoc"` 通過；config 讀得到預設值；
  既有測試不動。

## T2 — 型別化中介模型（`app/services/typst_render.py` 第一刀）

- `DocumentModel`：`blocks: [ProseBlock(typst_fragment) | KpiBlock(spec) | ChartBlock(spec)]`
  ＋ `meta: {title, date, question, sources}`。
- 切段：沿用 `pdf.py` 的 `_KPI_RE`/`_CHART_RE` 依**圍欄位置**切，不用占位符（D4）。
- 散文段 → `pypandoc.convert_text(..., to='typst', format='gfm-tex_math_dollars')`（D6，
  **這個 reader 參數不可省**，否則財經文本的 `$` 會誤判數學模式）。
- kpi/chart → 嚴格驗證：chart 用 `chart.py:_valid()`；kpi 比照 `pdf.py:inject_kpi` 的
  形狀檢查（items 非 list → 略過；非 dict 的 item → 跳過該項；>5 筆截斷）。
- **紅線（CLAUDE.md）**：每個欄位存取前先驗形狀，畸形 JSON 略過該區塊＋log，
  **例外絕不逃出**。
- **DoD**：單元測試覆蓋——正常 markdown 切段正確；11 種畸形 kpi/chart JSON（比照 M7
  probe）全部安全略過、零例外；敵意 fixture 轉出的 typst 片段所有注入向量已跳脫
  （`\#eval`、`\$`、`\@`）；`$100 美元` 不觸發數學模式。

## T3 — 模板契約

- 定義契約：模板接收 `DocumentModel` 中對應 §4 表列的固定區塊 + kpi/chart 資料。
- 章節切分：由 markdown 的 `## ` 標題映射到 M7 五章骨架（執行摘要／關鍵發現／
  重點分析／風險與展望／引用來源）＋可選的「外部參考（網路）」。
- **未知/多餘的 `##` 章節不得丟棄**——歸入「重點分析」或以泛用區塊承接（內容是真相，
  版型不得吃掉內容）。
- **DoD**：契約以 Typst 函式簽章 + Python data model 雙向固定；測試覆蓋「五章齊全」
  「缺章」「多出未知章節」「章節順序錯亂」四種 markdown 都不丟內容。

## T4 — `app/templates/ib-classic.typ`

- 國際投行密集雙欄；封面／頁首尾／KPI 卡／圖表 figure／引用來源懸掛縮排／免責。
- 字型：`Noto Serif CJK TC`（生產已裝）；**一律不用 italic**（CJK 無斜體字重，
  Typst 不合成 → 會退回無效果或怪異替代）。
- chart：`ChartBlock` → `chart.render_chart_svg()` → Typst `image()` 嵌入（D5）。
- 零 `@preview` 依賴（無網路編譯開箱即得）。
- **DoD**：`fixture_report.md` 編譯出 PDF，繁中正常、KPI/圖表/引用皆正確；
  `fixture_hostile.md` 編譯後注入向量全為字面文字。

## T5 — 接線與雙軌（`report.py`）

- `render_report_pdf` 的呼叫點依 `REPORT_RENDERER` 分派：`typst` → `typst_render`；
  `weasyprint` → 既有 `pdf.render_report_pdf`。
- **Typst 編譯失敗 → fail-open 回退 WeasyPrint**，log 原因，不 500。
- **patch 縫（本專案累犯點）**：`report.py` 對渲染函式的引用方式必須讓
  `tests/test_report.py` 既有的 `rpt.render_report_pdf` patch 仍攔得到生產路徑；
  新增的 typst 路徑也要有對應 patch 點。**不可出現「測試綠但生產走另一條」**。
- **DoD**：`REPORT_RENDERER` 兩值各出一份 PDF；編譯失敗 probe（故意餵壞模板）→
  回退成功、無 500、`report_doc` 正常持久化；`GenerateReportTests` 全綠。

## T6 — 免責兩軌（D1）

- 單一常數（例如 `REPORT_DISCLAIMER`）為兩軌唯一文字來源，避免漂移。
- Typst：模板契約固定區塊，恆存在、不依賴 LLM。
- WeasyPrint：`render_report_pdf` 注入。
- **DoD**：兩軌 PDF 都含免責；LLM markdown 完全沒提免責時仍存在；**回退路徑
  （typst 失敗 → weasyprint）的 PDF 也有免責**——這條要有獨立測試，因為它正是
  雷達改版時免責無聲消失的形態。

## T7 — 審查與收尾

- `code-reviewer` 審整支 diff。重點：形狀防禦紅線、patch 縫、pandoc reader 參數、
  免責兩軌一致、fail-open 邊界、Typst 注入面。
- 對抗式覆核：拿一份真研報 markdown 出 PDF，人工看五章/引用/KPI/圖表/免責/繁中。
- 開 PR（base main）。Commit 主體：`feat(渲染): 導入 Typst 渲染引擎與 ib-classic 模板`。

## 不做（M9b）

registry／`template_id` 貫穿／`report_rendition` 表／換皮重出端點／前端模板選擇器／
評等框（D2：`report_signal` 覆蓋率 0.71%，接上去是死區塊）。
