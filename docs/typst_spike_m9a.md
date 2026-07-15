# M9a Typst 渲染 spike 結論（2026-07-15）

## 決策

- **直接走 Typst**：捨棄 WeasyPrint 視覺化改版路線（`docs/superpowers/plans/2026-07-08-report-claude-design-backend.md` 的 5-task 計畫不執行），避免產生丟棄工。M9a 依 `docs/IMPLEMENTATION_PLAN.md` 進行，本文件是其前置 spike 的實證結論。
- **converter 選定 pandoc**（經 `pypandoc-binary` 取得，見下方比較）。
- **圖表首版直接重用 `chart.py` 的 SVG**（Typst `image()` 原生支援 SVG），不引入 cetz/lilaq，編譯全程零網路、零 `@preview` 套件。

## 環境現況（本機＝生產主機，2026-07-15 盤點）

| 項目 | 狀態 |
|------|------|
| 繁中字型 | 已安裝：`/usr/share/fonts/opentype/noto/` 含 Noto Serif CJK TC 與 Noto Sans CJK TC（Regular + Bold），先前 WeasyPrint 部署時已裝，**無需再裝** |
| typst CLI | 0.15.0 已裝至 `~/.local/bin/typst`（開發用；生產建議走 typst-py，見部署） |
| pandoc CLI | 3.10 已裝至 `~/.local/bin/pandoc`（開發用；生產建議走 pypandoc-binary） |

注意：CJK 字型無斜體字重（Typst 不會合成斜體），模板設計一律避免 italic，強調用粗體或色彩。

## converter 比較

fixtures 在 `docs/typst_spike/`：`fixture_report.md`（仿真研報：標題層級、GFM 表格、引言、清單、`[n]` 引用、kpi/chart 圍欄、外部連結）、`fixture_hostile.md`（Typst 語法注入與跳脫邊角：`#eval`/`#read`/`#import`/`#set`、`$` 數學模式、`@` 參照、方括號、反斜線、未閉合分隔符等）。

| 候選 | 實測結果 | 判定 |
|------|----------|------|
| **pandoc `gfm-tex_math_dollars` → typst** | 仿真研報全數正確（標題/表格/引言/清單/連結/繁中）；敵意 fixture 所有注入向量均被跳脫為字面文字（`\#eval`、`\$`、`\@`、`\[`、`\\`）；轉換約 12ms/篇 | **採用** |
| cmarker 0.1.6（`@preview` Typst 套件） | 可編譯、GFM 表格正常；但 markdown 在 Typst 編譯期才解析——Python 端無法建立型別化中介模型（違反 M9a 契約）、kpi/chart 插入笨拙、需下載套件（無網路 sandbox 要 vendor）、0.x 年輕套件 | 不採 |
| 自建 emitter（markdown-it-py） | 未實作。完全可控，但跳脫正確性要自己長期養（敵意 fixture 顯示邊角極多），維護風險高於收益 | 不採 |

### pandoc 的兩個已知行為（皆已有對策）

1. **`tex_math_dollars` 必須關閉**：GFM reader 預設把成對 `$...$` 解析成數學模式（財經文本的美元符號有機率誤配對成數學式）。實測 `-f gfm-tex_math_dollars` 關閉後，`$math$` 正確跳脫為字面 `\$math\$`。**M9a 實作必須帶此 reader 參數。**
2. **原生 HTML 會被丟棄**：`<div>`、`<br/>` 等原始 HTML 在 typst writer 輸出中消失。可接受——研報 prompt 契約要求純 markdown 輸出；此行為反而是額外的縱深防禦。

### 安全驗證（敵意 fixture 編譯後 PDF 逐項目檢）

- `#eval("1+1")`、`#read("/etc/passwd")`、`#import "@preview/evil:1.0.0"`、行首 `#set text(size: 30pt)`：全部以字面文字呈現，字級未被改變，未執行任何指令。
- `$100 美元`、`EPS $14.2`、`$880–$1,088`：無數學模式誤觸（關閉 tex_math_dollars 後）。
- 表格欄位內的 `|`、`#`、`$`、行內碼、巢狀清單、未閉合 `*` 與 `` ` ``：全部安全。

## M9a 實作建議架構（滿足「型別化中介資料模型」契約）

1. Python 端以既有 regex（`pdf.py` 的 `_KPI_RE`/`_CHART_RE`）抽出 ```` ```kpi ````/```` ```chart ```` 圍欄 → 嚴格驗證為型別化 `KpiSpec`/`ChartSpec`（沿用 `chart.py` 既有 `spec` 驗證）。
2. 其餘 markdown **依圍欄位置切段、逐段**經 pandoc `gfm-tex_math_dollars → typst` 轉為片段——不使用占位符（避免占位符被 pandoc 改寫的整類問題）。
3. 中介模型 `DocumentModel = [ProseBlock(typst_fragment) | KpiBlock(spec) | ChartBlock(spec)] + metadata（標題/日期/來源清單）`，由模板契約消費；**LLM 原文永不直接拼接進 Typst 原始碼**，只有 pandoc 跳脫後的片段與 JSON 驗證後的資料通過。
4. chart：`ChartBlock` 先走 `render_chart_svg()` 產 SVG → Typst `image()` 嵌入（實測含 SVG 內繁中文字渲染正確、`figure` 中文編號「圖 1」正常）。cetz 原生繪圖延後到有明確需求再評估。
5. 編譯：typst-py `typst.compile()` 回傳 PDF bytes（免暫存檔）、以 `root` 限制檔案存取；模板零 `@preview` 依賴 → 無網路編譯開箱即得。

## 實測數據

- pandoc 轉換：約 12ms/篇；typst CLI 編譯：亞秒；typst-py 編譯：0.20s（含字型載入）。對比 WeasyPrint 的秒級渲染有量級優勢。
- 視覺驗證 PDF：繁中煙霧測試、仿真研報、敵意輸入、SVG 圖表嵌入共四份，全部人工目檢通過。

## 部署清單（M9a 落地時）

- `uv add typst pypandoc-binary`（typst-py 實測 0.20s 編譯；pypandoc-binary 內建 pandoc 3.10 執行檔，敵意跳脫與 CLI 版一致）→ **零手動 binary 安裝、零 systemd PATH drop-in**（與 claude CLI 不同，渲染鏈不需要 PATH）。
- 字型：生產主機已具備；其他環境需安裝 Noto CJK（Serif TC 含 Regular+Bold 字重）。
- 維持 `REPORT_RENDERER=weasyprint|typst` 雙軌 flag 與 WeasyPrint 回退（IMPLEMENTATION_PLAN M9a 原契約）。

## 重現方式

```bash
# 轉換 + 編譯（開發機）
pandoc -f gfm-tex_math_dollars -t typst docs/typst_spike/fixture_report.md -o body.typ
printf '#set text(font: ("Noto Serif CJK TC",), lang: "zh", region: "TW")\n' | cat - body.typ > full.typ
typst compile full.typ

# 純 PyPI 路徑（生產形態）
uv run --with pypandoc-binary --with typst python -c "
import pypandoc, typst
out = pypandoc.convert_file('docs/typst_spike/fixture_hostile.md', 'typst', format='gfm-tex_math_dollars')
assert '\\\\#eval' in out"
```
