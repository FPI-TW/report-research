# report-mark 進階應用 Roadmap

把現有「研報語意檢索」升級為「問答 + 訊號 + 對外服務」平台。共四階段,前後有依賴。

> 詳細實作設計見規劃文件;本檔為階段里程碑視圖(非綁定日期)。

```
Phase 0 ──→ Phase 1 ──→ Phase 2 ──→ Phase 3
 基礎        智慧問答      訊號+findb     MCP/API
            (方向A)       (方向B)        (方向D)
              │             │              │
              └─ ask 服務 ──┴── 共識服務 ───┘  ← Phase 3 重用前兩階段
```

---

## Phase 0 — 共用基礎(所有方向的前置)

**目標**:建好設定、LLM 客戶端、認證、資料表、測試骨架,後續三階段都站在上面。

| 項目 | 交付物 |
|------|--------|
| 設定集中化 | `app/config.py`(pydantic-settings),`db.py` 改讀 settings |
| 依賴 | `anthropic`、`pydantic-settings`、`httpx`、`sse-starlette`、`mcp` |
| 統一 LLM 客戶端 | `app/services/llm.py`:SDK 串流(serving)+ CLI 包裝(批次) |
| 認證與安全 | `app/api/auth.py`:API key、prompt-injection 防護、速率限制 |
| 新資料表 | `daily_brief`、`report_signal`、`qa_log`(沿用 `db/schema.sql` 冪等模式) |
| 測試骨架 | mock LLM / embeddings 的 pytest fixtures |

**里程碑 M0**:`uv run pytest` 綠燈;設定與 LLM 客戶端可被新服務 import。

---

## Phase 1 — 智慧問答 / 簡報(方向 A)

**目標**:從「找報告」升級為「回答 + 摘要」。最快展現語料價值。

| 項目 | 交付物 |
|------|--------|
| RAG 問答服務 | `app/services/answer.py`(重用 `hybrid_search`,回答帶行內引用 → PDF) |
| 問答端點 | `POST /api/ask`(SSE 串流,掛認證,寫 `qa_log`) |
| 每日簡報 | `app/services/brief.py` + `scripts/daily_brief.py`(可排程) |
| 前端 | index.html 加「問答」模式;新增 `brief.html` |

**里程碑 M1**:`/api/ask` 串流回答且引用可連回原始 PDF;每日簡報可產出並快取。

---

## Phase 2 — 結構化訊號 + findb 整合(方向 B)

**目標**:把報告變成資料。工作量最大,可獨立批次跑。

| 項目 | 交付物 |
|------|--------|
| 抽取 schema | `app/services/signals.py`(評等 / 目標價 / EPS / 分析師,正規化) |
| 批次抽取管線 | `scripts/extract_signals.py`(仿 `tag_all_cli.py`,可續跑) |
| findb 客戶端 | `app/services/findb_client.py`(唯讀 Serve API:行情 + 名稱) |
| 共識聚合 | `app/services/consensus.py` + `/api/instrument/{code}`、`/api/consensus/{code}` |
| 前端 | `instrument.html`:報告時間軸 + 共識卡 + findb 價格疊圖 |
| (P2 延伸) | 券商準確度回測:目標價 vs findb 實現價 |

**里程碑 M2**:`/api/consensus/{code}` 回評等分布 + 相對 findb 收盤的 upside。

---

## Phase 3 — MCP / API 對外(方向 D)

**目標**:把語料與服務包成 agent / 外部工具可消費的介面。放最後接最完整。

| 項目 | 交付物 |
|------|--------|
| MCP server | `mcp_server/`:`search_reports` / `get_report` / `ask_reports` / `list_recent` / `get_consensus`(直接重用前兩階段服務層) |
| REST 對外 | `/api/v1/*`(認證 + 速率限制),FastAPI 自動 OpenAPI |
| 文件 | `docs/API.md`、`mcp_server/README.md` |

**里程碑 M3**:Claude Code 可載入 MCP server 並查詢;`/api/v1/*` 無金鑰 401、帶金鑰 200。

---

## 依賴與關鍵原則

- **Phase 0 必須先做**;Phase 1/2 可平行(若人力足),但 **Phase 3 依賴 1 與 2 的服務層**。
- **零重造檢索**:RAG 與 MCP 直接重用 `hybrid_search` / `list_reports`。
- **批次解耦**:訊號抽取仿既有 tagging 可續跑模式,獨立於主管線。
- **邊界乾淨**:findb 只走唯讀 Serve API,不直連其 DB。

## 暫不納入

掃描檔 OCR、GPU 加速 ingest、多帳號系統、通知/訂閱(方向 C)。
