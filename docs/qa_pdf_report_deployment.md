# 部署：問答 PDF 深度研報

問答答完後，助理在「適合時」主動建議出一份完整 PDF 深度研報；使用者點「要」即重跑深度檢索、串流生成結構化研報、以 WeasyPrint 輸出可下載 PDF，並隨對話輪次持久化（重開對話可重現下載）。

## 1. 相依與系統庫

- Python：`uv sync`（已含 `weasyprint`、`markdown`）。
- WeasyPrint 原生庫（Debian/Ubuntu）：

  ```bash
  sudo apt-get install -y \
    libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 libffi-dev
  ```

- **CJK 字型（缺則中文 PDF 變空白方塊／豆腐）**：

  ```bash
  sudo apt-get install -y fonts-noto-cjk
  fc-cache -f
  ```

  CSS 指定 `Noto Sans CJK TC`；若部署機已有其他繁中字型（如微軟正黑體），WeasyPrint 會透過 fontconfig fallback 使用，但建議仍裝 Noto CJK 以確保一致。

## 2. Schema

```bash
make schema
```

冪等新增 `research.report_doc`（id, qa_id, conversation_id, question, title, markdown, pdf_path, sources, thinking_ms, created_at）。**拉新碼前先套用**，避免 schema 漂移導致 `/api/report` 寫入失敗。

## 3. 設定（env，皆有預設，可不設）

| 變數 | 預設 | 說明 |
|------|------|------|
| `REPORT_MODEL` | `claude-sonnet-4-6` | 研報生成模型（長輸出用 Sonnet） |
| `REPORT_DEEP_K` | 30 | 深度檢索 k |
| `REPORT_MAX_REPORTS` | 25 | 研報脈絡最多篇數 |
| `REPORT_MAX_PASSAGES` | 6 | 每篇最多段數 |
| `REPORT_MAX_CONTEXT_CHARS` | 40000 | 脈絡總字數上限 |
| `REPORT_TIMEOUT` | 300 | 研報生成 LLM 串流逾時（秒）。深報為長輸出（實測常 ~200s），**勿低於 ~240**，否則會在逾時被靜默截斷（研報寫到一半就結束）。題材極廣可再調高。 |
| `REPORT_MIN_CITED` | 3 | 建議出研報的最低引用篇數 |
| `REPORT_SEMAPHORE` | 1 | 同時生成數（重任務，預設序列化） |
| `REPORTS_DIR` | `data/reports` | PDF 落地目錄（需可寫；建議與資料卷同盤、納入備份） |
| `REPORT_ENABLE_WEB` | `0` | 研報生成是否允許網路搜尋（預設關，研報以語料為據） |

注意：`generate_report` 會 spawn `claude` CLI（與 `/api/ask` 同），systemd 服務需確保 `claude` 在 PATH（沿用既有 web 服務的 PATH drop-in）。

## 4. 重啟

```bash
sudo systemctl restart report-mark-web.service
```

後端改動（新 endpoint、answer.py、report 模組）需重啟才生效；前端靜態檔（ask.js/index.html）由 `_NoCacheStatic` 即時供應，免重啟。

## 5. 驗證

```bash
# 相依可用
uv run python -c "import markdown; from weasyprint import HTML; print('deps ok')"
# CJK 字型存在
fc-list | grep -i cjk | head
# 表已建
docker.exe exec report-mark-postgres psql -U postgres -d research -c "\d research.report_doc"
```

UI 端到端：問一題分析題（例「請分析台積電近期的產業趨勢與未來展望」）→ 答完出現「要不要我幫你整理成一份完整 PDF 研報？」建議卡 → 點「要，幫我產生」→ 狀態列「深度檢索研報中…→撰寫研報中…→排版 PDF 中…」並即時預覽 → 出現「下載 PDF」→ 下載開啟確認**中文正常**、含金色品牌頁眉與頁碼 → 重開該對話確認研報卡重現可再下載。

## 6. 維運備註

- **PDF 落地與備份**：研報以 `markdown` 欄為真相來源，`REPORTS_DIR` 的 PDF 檔遺失時，`GET /api/report-doc/{id}/pdf` 會由 markdown 即時重建。故 `REPORTS_DIR` 不需特別備份（但 DB 的 `report_doc` 表需納入備份）。
- **無 TTL**：研報為保存成果，預設不自動清理；PDF 體積小（~150KB/篇）。日後若需控盤可加 age-based 清理（清 PDF 檔即可，markdown 仍可重建）。
- **限流**：`/api/report` 由 `REPORT_SEMAPHORE`（預設 1）序列化；區網多人同時請求會排隊（前端面板顯示生成中），避免同時多個長輸出 + PDF 排版拖垮機器。
