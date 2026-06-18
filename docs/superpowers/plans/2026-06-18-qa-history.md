# 問答歷史查看 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 問答模式內新增「歷史」抽層，讓使用者查看並唯讀重現過往問答（含可點 [n] 引用）。

**Architecture:** `qa_log` 補存 `sources jsonb`（當時完整來源含編號）→ 新端點 `GET /api/history` 回最近問答（排除離題拒答）→ 前端「歷史」抽層列出、點選以既有 `paintAnswer`/`paintSources` 唯讀重現。

**Tech Stack:** Python 3.13、FastAPI、PostgreSQL（jsonb，冪等 schema）、零工具鏈 `unittest`、原生 ESM 前端。

## Global Constraints

- 設定走 `os.getenv`；schema 用冪等 `ALTER ... ADD COLUMN IF NOT EXISTS`，套用以 `make schema` 或 `docker.exe exec ... psql`。
- 不修改 `hybrid_search`、意圖閘門、網搜整合、檢索頁；不新增第三方依賴；前端無 emoji。
- 歷史排除離題拒答：`answer IS DISTINCT FROM OFF_TOPIC_MESSAGE`。
- `sources` 欄存 `[asdict(s) for s in sources]` 即 `[{n, report_id, file_name, market, report_date}]`；舊列 null → 回 `[]`。
- 端點登入授權（沿用既有，全域套用）、唯讀；`limit` 夾 1..200、預設 50。
- 測試：`uv run python -m unittest tests.test_answer tests.test_intent -v`；前端 `node --check`。

---

### Task 1: `qa_log` 補存 sources（schema + `_log_qa` + `answer_question`）

**Files:**
- Modify: `db/schema.sql`（加 `sources jsonb` 冪等 ALTER）
- Modify: `app/services/answer.py`（`_log_qa` 加 `sources` 參數並 INSERT；三個呼叫點傳 sources）
- Test: `tests/test_answer.py`（新增 `LogQaSourcesTests`）

**Interfaces:**
- Produces: `_log_qa(question, answer, cited, filters, latency_ms, sources: list[dict]) -> str`（簽章新增末位 `sources`）

- [ ] **Step 1: schema 加欄**

在 `db/schema.sql` 的 `ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS feedback text;` 之後新增：

```sql
-- 當時完整來源（含編號），供歷史重現可點 [n]（冪等補欄）
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS sources jsonb;
```

- [ ] **Step 2: 寫失敗測試**

在 `tests/test_answer.py` 結尾（`if __name__` 之前）新增：

```python
class LogQaSourcesTests(unittest.IsolatedAsyncioTestCase):
    async def test_log_qa_inserts_sources_json(self):
        from app.services import answer as ans

        captured = {}

        class Sess:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, stmt, params=None):
                captured.update(params or {})

            async def commit(self):
                return None

        orig = ans.SessionFactory
        ans.SessionFactory = lambda: Sess()
        try:
            qid = await ans._log_qa("q", "a", [], {}, 5, [{"n": 1, "report_id": "r1"}])
        finally:
            ans.SessionFactory = orig

        self.assertTrue(qid)
        self.assertIn("sources", captured)
        self.assertEqual(json.loads(captured["sources"]), [{"n": 1, "report_id": "r1"}])
```

（檔案頂部已 `import sys`/`unittest`；`json` 需在測試檔可用——若未 import，於頂部 import 區加 `import json`。）

- [ ] **Step 3: 跑測試確認失敗**

Run: `uv run python -m unittest tests.test_answer.LogQaSourcesTests -v`
Expected: FAIL — `TypeError: _log_qa() takes 5 positional arguments but 6 were given`

- [ ] **Step 4: 改 `_log_qa` 簽章與 INSERT**

在 `app/services/answer.py`，把 `_log_qa` 改為（新增 `sources` 參數、INSERT 多寫 `sources`）：

```python
async def _log_qa(
    question: str,
    answer: str,
    cited: list[str],
    filters: dict,
    latency_ms: int,
    sources: list[dict],
) -> str:
    """寫一列 research.qa_log（best-effort：失敗不影響已回給使用者的答案）。

    回傳該列 id（即使寫入失敗仍回傳，供前端掛回饋；指向不存在列時 UPDATE 為 no-op）。
    sources 為當時完整來源（含編號），供歷史重現可點 [n]。
    """
    qa_id = str(uuid.uuid4())
    try:
        async with SessionFactory() as session:
            await session.execute(
                text(
                    "INSERT INTO research.qa_log "
                    "(id, question, answer, cited_report_ids, filters, latency_ms, sources) "
                    "VALUES (:id, :q, :a, :cited, :filters, :lat, :sources)"
                ),
                {
                    "id": qa_id,
                    "q": question,
                    "a": answer,
                    "cited": cited,  # uuid[]：asyncpg 由欄位型別推斷，傳 list[str]
                    "filters": json.dumps(filters, ensure_ascii=False),  # jsonb
                    "lat": latency_ms,
                    "sources": json.dumps(sources, ensure_ascii=False),  # jsonb
                },
            )
            await session.commit()
    except Exception:
        pass
    return qa_id
```

- [ ] **Step 5: 三個呼叫點傳 sources**

在 `answer_question` 中，把三處 `_log_qa(...)` 呼叫補上末位引數：
- 離題分支：`await _log_qa(question, OFF_TOPIC_MESSAGE, [], filters, int((time.monotonic()-started)*1000), [])`
- 無脈絡分支：`qa_id = await _log_qa(question, NO_CONTEXT_MESSAGE, [], filters, int((time.monotonic()-started)*1000), [])`
- 主回答分支：`qa_id = await _log_qa(question, body, cited, filters, int((time.monotonic()-started)*1000), [asdict(s) for s in sources])`

（`asdict` 已 import；`sources` 為 `build_context` 回的 `list[Source]`。三處原本的多行呼叫，把參數補在最後即可。）

- [ ] **Step 6: 跑測試確認通過 + 全套**

Run: `uv run python -m unittest tests.test_answer.LogQaSourcesTests -v`
Expected: PASS
Run: `uv run python -m unittest tests.test_answer tests.test_intent -v`
Expected: PASS（既有 AnswerGate/AnswerWeb 等全綠——它們 monkeypatch `_log_qa` 或用假 session，不受簽章影響；若有直接呼叫 `_log_qa` 的既有測試，補末位 `[]`）

- [ ] **Step 7: Commit**

```bash
git add db/schema.sql app/services/answer.py tests/test_answer.py
git commit -m "$(cat <<'EOF'
feat(ask): qa_log 補存 sources jsonb（供歷史重現可點 [n]）

schema 冪等加 sources 欄；_log_qa 多收 sources 並寫入；answer_question
主回答傳實際來源、離題/無脈絡傳 []。新增 LogQaSourcesTests。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `GET /api/history` 端點 + 純轉換

**Files:**
- Modify: `app/services/answer.py`（新增純函式 `history_item`）
- Modify: `web/server.py`（新增 `/api/history` 端點、import `OFF_TOPIC_MESSAGE`/`history_item`）
- Test: `tests/test_answer.py`（新增 `HistoryItemTests`）

**Interfaces:**
- Consumes: `OFF_TOPIC_MESSAGE`、`history_item`
- Produces: `history_item(row) -> dict`（row=(id, question, answer, created_at, feedback, sources)）

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_answer.py` import 區補入 `history_item`：

```python
from app.services.answer import (  # noqa: E402
    Source,
    build_context,
    build_user_prompt,
    cited_report_ids,
    history_item,
    split_external_sources,
)
```

新增測試類別：

```python
class HistoryItemTests(unittest.TestCase):
    def test_maps_row_with_sources(self):
        d = date(2026, 6, 18)
        row = ("11111111-1111-1111-1111-111111111111", "台積電?", "答案[1]",
               d, "like", [{"n": 1, "report_id": "r1", "file_name": "甲.pdf"}])
        out = history_item(row)
        self.assertEqual(out["id"], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(out["question"], "台積電?")
        self.assertEqual(out["answer"], "答案[1]")
        self.assertEqual(out["created_at"], "2026-06-18")
        self.assertEqual(out["feedback"], "like")
        self.assertEqual(out["sources"], [{"n": 1, "report_id": "r1", "file_name": "甲.pdf"}])

    def test_null_sources_becomes_empty_list(self):
        row = ("id2", "q", "a", date(2026, 6, 1), None, None)
        out = history_item(row)
        self.assertEqual(out["sources"], [])
        self.assertIsNone(out["feedback"])
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run python -m unittest tests.test_answer.HistoryItemTests -v`
Expected: FAIL — `ImportError: cannot import name 'history_item'`

- [ ] **Step 3: 實作 `history_item`**

在 `app/services/answer.py` 的 `cited_report_ids` 之後新增：

```python
def history_item(row) -> dict:
    """qa_log 一列 (id, question, answer, created_at, feedback, sources) → 前端用 dict。

    sources 為 None（舊列）時回 []；created_at 轉 ISO 字串。
    """
    id_, question, answer, created_at, feedback, sources = row
    created = created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
    return {
        "id": str(id_),
        "question": question,
        "answer": answer,
        "created_at": created,
        "feedback": feedback,
        "sources": sources or [],
    }
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run python -m unittest tests.test_answer.HistoryItemTests -v`
Expected: PASS（2 tests）

- [ ] **Step 5: 加 `/api/history` 端點**

在 `web/server.py`，把 answer 的 import 行改為一併匯入（找現有 `from app.services.answer import answer_question, record_feedback  # noqa: E402`）：

```python
from app.services.answer import (  # noqa: E402
    OFF_TOPIC_MESSAGE,
    answer_question,
    history_item,
    record_feedback,
)
```

在 `/api/feedback` 端點之後新增：

```python
@app.get("/api/history")
async def history(limit: int = Query(50, ge=1, le=200)):
    """最近的問答歷史（排除離題拒答）；唯讀，供前端「歷史」抽層。"""
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, question, answer, created_at, feedback, sources "
                    "FROM research.qa_log "
                    "WHERE answer IS DISTINCT FROM :offtopic "
                    "ORDER BY created_at DESC LIMIT :limit"
                ),
                {"offtopic": OFF_TOPIC_MESSAGE, "limit": limit},
            )
        ).all()
    return [history_item(tuple(r)) for r in rows]
```

- [ ] **Step 6: 跑全套 + 啟動煙霧測試**

Run: `uv run python -m unittest tests.test_answer tests.test_intent -v`
Expected: PASS
Run: `uv run python -c "import ast; ast.parse(open('web/server.py').read())" && echo "server.py OK"`
Expected: `server.py OK`

- [ ] **Step 7: Commit**

```bash
git add app/services/answer.py web/server.py tests/test_answer.py
git commit -m "$(cat <<'EOF'
feat(ask): GET /api/history 端點 + history_item 轉換

回最近問答（排除離題拒答、limit 1..200）；history_item 純函式把 qa_log 列
轉前端 dict（sources null→[]、created_at iso）。新增 HistoryItemTests。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: 前端「歷史」抽層 + 唯讀重現

**Files:**
- Modify: `web/static/app/ask.js`（歷史鈕圖示、`openHistory`/`renderHistory`/`loadHistoryItem`/`closeHistory`、initAsk 接線）
- Modify: `web/static/index.html`（歷史鈕 + 抽層 markup + CSS）

**Interfaces:**
- Consumes: `GET /api/history`（`[{id, question, answer, created_at, feedback, sources}]`）；既有 `paintAnswer`/`paintSources`/`paintExtSources`/`paintActions`/`fmtDate`/`html`/`raw`/模組 `sources`/`extSources`

- [ ] **Step 1: index.html 加歷史鈕與抽層 markup**

在 `web/static/index.html` 的 composer，把現有 `.ask-composer-inner` 內容前面加歷史鈕（找 `<textarea id="askInput"` 那行，於其前插入）：

```html
              <button class="ask-hist-btn" id="askHistBtn" type="button" title="歷史問答" aria-label="歷史問答">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3v5h5"/><path d="M3.05 13A9 9 0 1 0 6 5.3L3 8"/><path d="M12 7v5l3 2"/></svg>
              </button>
```

在 `.ask-panel`（`<div class="ask-panel" id="askPanel" hidden>` … `</div>`）的結尾 `</div>` 之前（composer 之後）加抽層：

```html
          <div class="ask-hist-drawer" id="askHistDrawer" hidden>
            <div class="ask-hist-backdrop" id="askHistBackdrop"></div>
            <aside class="ask-hist-panel" role="dialog" aria-modal="true" aria-label="歷史問答">
              <div class="ask-hist-head">
                <span class="ask-hist-title">歷史問答</span>
                <button class="ask-hist-close" id="askHistClose" type="button" aria-label="關閉歷史">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
              </div>
              <div class="ask-hist-list" id="askHistList"></div>
            </aside>
          </div>
```

- [ ] **Step 2: index.html 加抽層 CSS**

在 ask 相關 CSS 區（如 `.ask-searching-inline` 規則之後）新增：

```css
  /* 歷史鈕（composer 左側）*/
  .ask-hist-btn { flex: none; display: inline-flex; align-items: center; justify-content: center;
    width: 42px; height: 42px; border: none; border-radius: var(--radius-sm); cursor: pointer;
    background: var(--fill); color: var(--label-2); }
  .ask-hist-btn:hover { background: #78788022; color: var(--label); }
  .ask-hist-btn svg { width: 19px; height: 19px; }

  /* 歷史抽層（右側覆蓋）*/
  .ask-hist-drawer { position: fixed; inset: 0; z-index: 50; }
  .ask-hist-drawer[hidden] { display: none; }
  .ask-hist-backdrop { position: absolute; inset: 0; background: rgba(0,0,0,.28); }
  .ask-hist-panel { position: absolute; top: 0; right: 0; height: 100dvh; width: min(420px, 92vw);
    background: var(--card); box-shadow: -8px 0 30px rgba(0,0,0,.12); display: flex; flex-direction: column;
    padding: max(env(safe-area-inset-top), 16px) 16px 16px; }
  .ask-hist-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  .ask-hist-title { font-size: 17px; font-weight: 700; color: var(--label); }
  .ask-hist-close { border: none; background: none; cursor: pointer; color: var(--label-3);
    width: 32px; height: 32px; display: grid; place-items: center; border-radius: 8px; }
  .ask-hist-close:hover { background: var(--fill); color: var(--label); }
  .ask-hist-close svg { width: 18px; height: 18px; }
  .ask-hist-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
  .ask-hist-empty { color: var(--label-3); font-size: 14px; text-align: center; padding: 28px 0; }
  .ask-hist-item { display: flex; flex-direction: column; gap: 5px; width: 100%; text-align: left;
    background: var(--fill); border: none; border-radius: 11px; padding: 12px 14px; cursor: pointer;
    font-family: inherit; color: var(--label); }
  .ask-hist-item:hover { background: #78788022; }
  .ask-hist-q { font-size: 14.5px; line-height: 1.5; overflow: hidden; text-overflow: ellipsis;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
  .ask-hist-meta { display: flex; align-items: center; gap: 8px; }
  .ask-hist-date { font-size: 12px; color: var(--label-3); }
  .ask-hist-fb { font-size: 11px; font-weight: 700; padding: 1px 6px; border-radius: 5px; }
  .ask-hist-fb.like { color: var(--blue); background: var(--blue-soft); }
  .ask-hist-fb.dislike { color: var(--red); background: #ff3b301f; }
```

- [ ] **Step 3: ask.js 加抽層邏輯**

在 `web/static/app/ask.js` 的 `paintExtSources` 之後新增：

```javascript
async function openHistory() {
  const drawer = $("#askHistDrawer");
  const list = $("#askHistList");
  drawer.hidden = false;
  list.innerHTML = `<div class="ask-hist-empty">載入中…</div>`;
  try {
    const resp = await fetch("/api/history?limit=50");
    if (resp.status === 401) { window.location.href = "/login"; return; }
    if (!resp.ok) throw new Error("bad");
    renderHistory(await resp.json());
  } catch (e) {
    list.innerHTML = `<div class="ask-hist-empty">載入失敗，請稍後再試。</div>`;
  }
}
function closeHistory() { $("#askHistDrawer").hidden = true; }

function renderHistory(items) {
  const list = $("#askHistList");
  if (!items.length) { list.innerHTML = `<div class="ask-hist-empty">尚無歷史問答</div>`; return; }
  list.innerHTML = items.map((it, i) => html`<button class="ask-hist-item" type="button" data-i="${String(i)}">
      <span class="ask-hist-q">${it.question}</span>
      <span class="ask-hist-meta">
        ${it.created_at ? html`<span class="ask-hist-date">${fmtDate(it.created_at)}</span>` : raw("")}
        ${it.feedback === "like" ? html`<span class="ask-hist-fb like">讚</span>`
          : it.feedback === "dislike" ? html`<span class="ask-hist-fb dislike">倒讚</span>` : raw("")}
      </span>
    </button>`).join("");
  list.querySelectorAll(".ask-hist-item").forEach(b =>
    b.onclick = () => loadHistoryItem(items[parseInt(b.dataset.i, 10)]));
}

// 唯讀重現一筆歷史問答（沿用既有渲染；不重打 /api/ask）
function loadHistoryItem(it) {
  closeHistory();
  $("#askEmpty").hidden = true;
  $("#askQuestion").hidden = false; $("#askQuestion").textContent = it.question;
  $("#askAnswer").hidden = false;
  sources = it.sources || [];
  extSources = [];
  paintSources(sources);
  paintExtSources([]);
  paintAnswer(it.answer || "", false);
  paintActions(it.id, it.answer || "", sources.length, 0);
  if (it.feedback) {   // 預先高亮當時回饋（可改）
    const sel = it.feedback === "like" ? "[data-act='like']" : "[data-act='dislike']";
    const btn = document.querySelector("#askActions " + sel);
    if (btn) btn.classList.add("on");
  }
  toBottom();
}
```

- [ ] **Step 4: ask.js 在 initAsk 接線**

在 `initAsk()` 內（`$("#askGo").onclick = ...` 附近）新增：

```javascript
  $("#askHistBtn").onclick = openHistory;
  $("#askHistClose").onclick = closeHistory;
  $("#askHistBackdrop").onclick = closeHistory;
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && !$("#askHistDrawer").hidden) closeHistory();
  });
```

- [ ] **Step 5: 語法檢查**

Run: `node --check web/static/app/ask.js && echo "ask.js OK"`
Expected: `ask.js OK`

- [ ] **Step 6: Commit**

```bash
git add web/static/app/ask.js web/static/index.html
git commit -m "$(cat <<'EOF'
feat(ask): 前端歷史問答抽層 + 唯讀重現

問答頁加「歷史」鈕 → 右側抽層列出過往問答（問題/日期/讚倒讚徽章）；
點一筆以既有 paintAnswer/paintSources 唯讀重現（[n] 可點開原報告）、動作列
預先高亮當時回饋可改。Esc/背景/關閉鈕收起。無 emoji、沿用設計 token。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: 部署與端到端驗證（手動）

**Files:** 無程式碼變更

- [ ] **Step 1: 套用 schema 並重啟 8097**

```bash
cd /mnt/c/Users/User/Desktop/Project/report-mark
docker.exe exec -i report-mark-postgres psql -U postgres -d research -c "ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS sources jsonb;"
OLD=$(ss -tlnp 2>/dev/null | grep -oP '8097.*pid=\K[0-9]+' | head -1); kill "$OLD"; sleep 2
setsid nohup uv run uvicorn web.server:app --host 0.0.0.0 --port 8097 > data/web_8097.log 2>&1 < /dev/null &
```
Expected: 欄位已存在、`Application startup complete`。

- [ ] **Step 2: 產生一筆新歷史**

Playwright 登入 → 問答模式問一題研報題（如「台積電的展望如何」）→ 等回答完成。

- [ ] **Step 3: 開歷史抽層驗證**

點「歷史」鈕。Expected：抽層滑出、剛問的題在列（問題 + 日期）；若按過讚有徽章。

- [ ] **Step 4: 點選重現驗證**

點該筆。Expected：抽層收起、主區重現問題/答案/來源；答案中 `[n]` 可點開原報告 modal；動作列回饋狀態正確、可改。

- [ ] **Step 5: 離題不入歷史**

問「我想喝飲料推薦給我」→ 開歷史，確認該離題題**不在**清單。

---

## Self-Review

- **Spec coverage**：sources jsonb + _log_qa → Task 1；/api/history + 過濾 + history_item → Task 2；抽層 + 唯讀重現 + [n] 可點 → Task 3；部署/驗證 → Task 4。皆有對應。
- **Placeholder scan**：各步含實際程式碼與指令；無 TBD/TODO。
- **Type consistency**：`_log_qa(...,sources)`、`history_item(row)->dict`、`/api/history` 回 `[{id,question,answer,created_at,feedback,sources}]`、前端 `loadHistoryItem(it)` 用同欄位、`sources` 模組變數驅動 `[n]` 點擊，跨任務一致。
- **既有測試**：`_log_qa` 簽章新增末位參數——既有測試多 monkeypatch `_log_qa`（不受影響）；若有直接呼叫者，Task 1 Step 6 已提醒補 `[]`。離題/無脈絡仍傳 `[]`、行為不變。
