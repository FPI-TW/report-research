# 問答「已思考 XX 秒」動態思考列 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把問答「處理過程」面板標題升級成 ChatGPT 式動態思考列：處理中顯示「正在思考 ↔ 目前動作」、結束結算「已思考 XX 秒」、點開看做過哪些動作，即時與歷史用同一個後端量得的秒數。

**Architecture:** 後端 `answer_question` 在「第一個 token」邊界量測思考時間 `thinking_ms`（開始→第一個 token），以新事件 `("status", {"stage":"generating","thinking_ms":N})` 帶給前端、並寫入 `qa_log.thinking_ms`（單一真實來源，即時＝歷史一致）。前端把標題列改成「狀態圖示＋動態標籤」狀態機（取代秒數計時器），收動作事件閃示動作標籤後還原「正在思考」，收 `generating` 凍結成「已思考 X 秒」。

**Tech Stack:** Python 3.13 / FastAPI / async SQLAlchemy(`text()` raw SQL) / asyncpg；前端原生 ESM（零工具鏈，`web/static/app/`）＋ index.html 內嵌 `<style>`；測試 `unittest`（`IsolatedAsyncioTestCase`，mock `stream_completion`/`SessionFactory`）。

## Global Constraints

- UI 文案一律**繁體中文**；圖示一律 **inline SVG，不用 emoji**（沿用 `PROC_ICO`/`SVG`）。
- 前端**零建置工具鏈**：原生 ES module，不得引入打包器或新相依。
- DB 一律 **async**、raw SQL 包 `text()`、明確 `commit()`；schema 變更走 `db/schema.sql`（冪等 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`），不另用 Alembic（本專案 web 端 schema 以 `make schema` 套用）。
- `qa_log` 既有 `latency_ms`＝總耗時，**不得挪用**；新增獨立 `thinking_ms`（int，nullable）。
- 秒數量測語意：`thinking_ms` ＝ `answer_question` 開始（`started = time.monotonic()`）→ 第一個 token。
- commit 前綴沿用專案慣例：`feat(ask):` / `fix(ask):` / `test(ask):` / `docs(ask):`。
- `EXT_SENTINEL = "[EXT_SOURCES]"`（長度 13）；token 切割三處 yield 點。
- 部署：後端與 schema 變更需 `make schema` ＋ 重啟 `report-mark-web.service`；前端 `/static` no-cache 即時生效。

---

## File Structure

| 檔案 | 責任 | 動作 |
|------|------|------|
| `app/services/answer.py` | `answer_question` 量測並 emit `generating(thinking_ms)`、三路徑 `done` 帶 `thinking_ms`；`_log_qa` 寫入；`history_item` 序列化；`get_conversation` SELECT | 修改 |
| `db/schema.sql` | `research.qa_log` 新增 `thinking_ms int`（CREATE 內＋冪等 ALTER） | 修改 |
| `web/server.py` | `/api/history` SELECT 加 `thinking_ms` | 修改 |
| `tests/test_answer.py` | 事件序（generating/done/thinking_ms）與 `history_item` 序列化測試 | 修改 |
| `web/static/app/ask.js` | 標題列動態標籤狀態機（`setHead`/`flashHead`/`freezeHead`/`thinkingLabel`）、`renderProcess` 預設收合、`onStatus`/token/`staticProcess`/`loadConversation`/`createTurn`/`clearProcess`/`finishProcess` 調整 | 修改 |
| `web/static/index.html` | 標題列圖示 CSS（`.ask-proc-ico`、`.is-done`） | 修改 |

---

## Task 1: 後端 `answer_question` 發 generating(thinking_ms) 並寫入

**Files:**
- Modify: `app/services/answer.py:295-339`（`_log_qa` 簽章＋INSERT）
- Modify: `app/services/answer.py:525-539`（離題路徑）
- Modify: `app/services/answer.py:545-558`（無脈絡路徑）
- Modify: `app/services/answer.py:560-605`（主串流路徑）
- Test: `tests/test_answer.py`

**Interfaces:**
- Consumes: 既有 `started = time.monotonic()`（answer.py:497）、`EXT_SENTINEL`、`stream_completion`、`SEARCH_EVENT`、`NO_CONTEXT_MESSAGE`、`OFF_TOPIC_MESSAGE`。
- Produces:
  - 新事件 `("status", {"stage": "generating", "thinking_ms": <int>})`，在每條會輸出 token 的路徑於**第一個 token 之前**發一次。
  - 三路徑 `done` payload 皆含 `"thinking_ms": <int>`。
  - `_log_qa(..., *, conversation_id=None, thinking_ms: int | None = None)` 並把 `thinking_ms` 寫入 `qa_log`。

- [ ] **Step 1: 寫失敗測試（generating + done 帶 thinking_ms）**

在 `tests/test_answer.py` 的 `test_emits_process_status_steps`（約 line 408）之後，於同一 class 內新增：

```python
    async def test_emits_generating_with_thinking_ms(self):
        # 正常路徑：第一個 token 前發 generating 帶 int thinking_ms；done 亦帶 thinking_ms
        from app.services import answer as ans

        called = {"llm": False, "intent": False}
        orig = self._patch(ans, in_domain=True, called=called)
        try:
            events = [e async for e in ans.answer_question("可口可樂的投資評級如何")]
        finally:
            self._restore(ans, orig)

        i_gen = next(
            i
            for i, (k, p) in enumerate(events)
            if k == "status" and isinstance(p, dict) and p.get("stage") == "generating"
        )
        i_token = next(i for i, (k, _) in enumerate(events) if k == "token")
        self.assertLess(i_gen, i_token)  # generating 在第一個 token 之前
        self.assertIsInstance(events[i_gen][1]["thinking_ms"], int)
        self.assertEqual(events[-1][0], "done")
        self.assertIsInstance(events[-1][1]["thinking_ms"], int)  # done 帶 thinking_ms
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_answer.py -k test_emits_generating_with_thinking_ms -v`
Expected: FAIL（找不到 `generating` 事件，`StopIteration`／`KeyError: 'thinking_ms'`）

- [ ] **Step 3: 改 `_log_qa` 簽章與 INSERT 寫入 thinking_ms**

`app/services/answer.py`，把 `_log_qa` 定義（line 295-305）改為：

```python
async def _log_qa(
    question: str,
    answer: str,
    cited: list[str],
    filters: dict,
    latency_ms: int,
    sources: list[dict],
    ext_sources: list[dict] | None = None,
    *,
    conversation_id: str | None = None,
    thinking_ms: int | None = None,
) -> str:
```

並把 INSERT（line 317-322）與參數（line 324-334）改為：

```python
                text(
                    "INSERT INTO research.qa_log "
                    "(id, question, answer, cited_report_ids, filters, latency_ms, "
                    "sources, ext_sources, conversation_id, thinking_ms) "
                    "VALUES (:id, :q, :a, :cited, :filters, :lat, "
                    ":sources, :ext_sources, :conv, :think)"
                ),
                {
                    "id": qa_id,
                    "q": question,
                    "a": answer,
                    "cited": cited,
                    "filters": json.dumps(filters, ensure_ascii=False),
                    "lat": latency_ms,
                    "sources": json.dumps(sources, ensure_ascii=False),
                    "ext_sources": json.dumps(ext_sources, ensure_ascii=False),
                    "conv": conversation_id,
                    "think": thinking_ms,
                },
```

- [ ] **Step 4: 主串流路徑 emit generating 並傳 thinking_ms**

把 `app/services/answer.py` 主串流區塊（line 560-605，自 `user_prompt = build_user_prompt(...)` 起至 `yield ("done", ...)` 止）整段替換為：

```python
    user_prompt = build_user_prompt(question, context, history_block)
    raw_parts: list[str] = []
    buf = ""
    hold = len(EXT_SENTINEL)
    sentinel_found = False
    searching_sent = False
    thinking_ms: int | None = None

    def _emit_token(piece: str) -> list[tuple[str, object]]:
        """首個 token 前補發 generating(thinking_ms)，回傳要 yield 的事件序。"""
        nonlocal thinking_ms
        out: list[tuple[str, object]] = []
        if thinking_ms is None:
            thinking_ms = int((time.monotonic() - started) * 1000)
            out.append(
                ("status", {"stage": "generating", "thinking_ms": thinking_ms})
            )
        out.append(("token", piece))
        return out

    yield ("status", {"stage": "reading"})  # 步驟3：閱讀重點、整理回答
    async for chunk in stream_completion(
        user_prompt, model=model, system=SYSTEM_PROMPT, allow_web=ASK_ENABLE_WEB
    ):
        if chunk == SEARCH_EVENT:
            if not searching_sent:
                searching_sent = True
                yield ("status", {"stage": "searching_web"})  # 步驟4：搜尋網路補充
            continue
        raw_parts.append(chunk)
        if sentinel_found:
            continue
        buf += chunk
        idx = buf.find(EXT_SENTINEL)
        if idx != -1:
            if buf[:idx]:
                for ev in _emit_token(buf[:idx]):
                    yield ev
            sentinel_found = True
            buf = ""
        elif len(buf) > hold:
            for ev in _emit_token(buf[:-hold]):
                yield ev
            buf = buf[-hold:]
    if not sentinel_found and buf:
        for ev in _emit_token(buf):
            yield ev

    raw = "".join(raw_parts)
    body, ext_sources = split_external_sources(raw)
    cited = cited_report_ids(body, sources)
    yield ("ext_sources", ext_sources)
    qa_id = await _log_qa(
        question,
        body,
        cited,
        filters,
        int((time.monotonic() - started) * 1000),
        [asdict(s) for s in sources],
        ext_sources,
        conversation_id=conv_id,
        thinking_ms=thinking_ms,
    )
    yield (
        "done",
        {
            "cited": cited,
            "qa_id": qa_id,
            "conversation_id": conv_id,
            "thinking_ms": thinking_ms,
        },
    )
```

- [ ] **Step 5: 跑新測試確認通過**

Run: `uv run pytest tests/test_answer.py -k test_emits_generating_with_thinking_ms -v`
Expected: PASS

- [ ] **Step 6: 無脈絡與離題路徑也帶 thinking_ms（先改測試）**

把 `tests/test_answer.py::test_no_context_emits_retrieved_zero_without_reading` 的事件序斷言（約 line 399-405）改為：

```python
        kinds = [k for k, _ in events]
        self.assertEqual(
            kinds, ["status", "sources", "status", "status", "token", "done"]
        )
        self.assertEqual(events[0], ("status", {"stage": "understanding"}))
        self.assertEqual(events[2], ("status", {"stage": "retrieved", "count": 0}))
        # 無脈絡路徑不得發 reading
        self.assertNotIn(("status", {"stage": "reading"}), events)
        # NO_CONTEXT token 前補發 generating，帶 thinking_ms
        self.assertEqual(events[3][0], "status")
        self.assertEqual(events[3][1]["stage"], "generating")
        self.assertIn("thinking_ms", events[3][1])
        self.assertEqual(events[4], ("token", ans.NO_CONTEXT_MESSAGE))
        self.assertFalse(called["llm"])  # 未跑主 LLM
```

- [ ] **Step 7: 跑該測試確認失敗**

Run: `uv run pytest tests/test_answer.py -k test_no_context_emits_retrieved_zero_without_reading -v`
Expected: FAIL（目前序列為 `["status","sources","status","token","done"]`，缺 generating）

- [ ] **Step 8: 改無脈絡與離題路徑**

把 `app/services/answer.py` 離題路徑（line 525-539）替換為：

```python
    if not in_domain:  # 離題：拒答、不跑主 LLM
        yield ("sources", [])
        yield ("notice", OFF_TOPIC_MESSAGE)
        thinking_ms = int((time.monotonic() - started) * 1000)
        await _log_qa(
            question,
            OFF_TOPIC_MESSAGE,
            [],
            filters,
            thinking_ms,
            [],
            [],
            conversation_id=conv_id,
            thinking_ms=thinking_ms,
        )
        yield (
            "done",
            {"cited": [], "conversation_id": conv_id, "thinking_ms": thinking_ms},
        )
        return
```

把無脈絡路徑（line 545-558）替換為：

```python
    if not context:
        thinking_ms = int((time.monotonic() - started) * 1000)
        yield ("status", {"stage": "generating", "thinking_ms": thinking_ms})
        yield ("token", NO_CONTEXT_MESSAGE)
        qa_id = await _log_qa(
            question,
            NO_CONTEXT_MESSAGE,
            [],
            filters,
            thinking_ms,
            [],
            [],
            conversation_id=conv_id,
            thinking_ms=thinking_ms,
        )
        yield (
            "done",
            {
                "cited": [],
                "qa_id": qa_id,
                "conversation_id": conv_id,
                "thinking_ms": thinking_ms,
            },
        )
        return
```

- [ ] **Step 9: 跑整檔測試確認全通過**

Run: `uv run pytest tests/test_answer.py -v`
Expected: PASS（含原有 `test_off_topic_intent_skips_llm`、`test_on_topic_intent_calls_llm`、`test_emits_process_status_steps`；它們以 `assertIn`/索引斷言，新增 generating 與 done 的 `thinking_ms` 不破壞）

- [ ] **Step 10: Commit**

```bash
git add app/services/answer.py tests/test_answer.py
git commit -m "$(cat <<'EOF'
feat(ask): answer_question 於第一個 token 發 generating 帶 thinking_ms

思考時間＝開始→第一個 token，於主串流/無脈絡路徑 token 前補發
generating(thinking_ms)，三路徑 done 皆帶 thinking_ms，_log_qa 寫入。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `qa_log.thinking_ms` schema 與歷史序列化

**Files:**
- Modify: `db/schema.sql:77-85`（CREATE TABLE qa_log 加欄）＋ qa_log 區附近加冪等 ALTER
- Modify: `app/services/answer.py:271-292`（`history_item`）
- Modify: `app/services/answer.py:412-426`（`get_conversation` SELECT）
- Modify: `web/server.py:619-624`（`/api/history` SELECT）
- Test: `tests/test_answer.py`

**Interfaces:**
- Consumes: Task 1 已寫入的 `qa_log.thinking_ms`。
- Produces: `history_item(row)` 回傳 dict 多一鍵 `"thinking_ms": int | None`；`get_conversation`／`/api/history` 的 row 末欄為 `thinking_ms`。

- [ ] **Step 1: 寫失敗測試（history_item 序列化）**

在 `tests/test_answer.py` 檔末新增一個獨立 class：

```python
class HistoryItemThinkingTests(unittest.TestCase):
    def test_history_item_includes_thinking_ms(self):
        from app.services.answer import history_item

        row = ("id1", "q", "a", "2026-06-22T00:00:00+00:00", None, [], [], 1234)
        item = history_item(row)
        self.assertEqual(item["thinking_ms"], 1234)

    def test_history_item_thinking_ms_none_for_old_rows(self):
        from app.services.answer import history_item

        # 舊列（7 欄，無 thinking_ms）→ 回 None，不報錯
        row = ("id1", "q", "a", "2026-06-22T00:00:00+00:00", None, [], [])
        item = history_item(row)
        self.assertIsNone(item["thinking_ms"])
```

確認檔案頂部已有 `import unittest`（既有測試已用，無需新增）。

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_answer.py -k HistoryItemThinking -v`
Expected: FAIL（`KeyError: 'thinking_ms'`）

- [ ] **Step 3: 改 `history_item` 解析第 8 欄**

把 `app/services/answer.py` 的 `history_item`（line 271-292）整個函式替換為：

```python
def history_item(row) -> dict:
    """qa_log 一列 → 前端用 dict。

    相容舊列（6 欄無 ext_sources、7 欄無 thinking_ms）與新列（8 欄）。
    sources/ext_sources 為 None 時回 []；thinking_ms 缺欄回 None。
    created_at 轉 ISO 字串；離題拒答額外標記 is_offtopic，供前端重播時維持 notice 呈現。
    """
    thinking_ms = None
    if len(row) >= 8:
        (
            id_,
            question,
            answer,
            created_at,
            feedback,
            sources,
            ext_sources,
            thinking_ms,
        ) = row[:8]
    elif len(row) >= 7:
        id_, question, answer, created_at, feedback, sources, ext_sources = row[:7]
    else:
        id_, question, answer, created_at, feedback, sources = row[:6]
        ext_sources = None
    created = created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
    return {
        "id": str(id_),
        "question": question,
        "answer": answer,
        "created_at": created,
        "feedback": feedback,
        "sources": sources or [],
        "ext_sources": ext_sources or [],
        "is_offtopic": answer == OFF_TOPIC_MESSAGE,
        "thinking_ms": thinking_ms,
    }
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_answer.py -k HistoryItemThinking -v`
Expected: PASS

- [ ] **Step 5: 兩處 SELECT 加 thinking_ms 末欄**

`app/services/answer.py` 的 `get_conversation`（line 418-421）SELECT 改為：

```python
                    "SELECT id, question, answer, created_at, feedback, sources, ext_sources, thinking_ms "
                    "FROM research.qa_log "
                    "WHERE COALESCE(conversation_id, id) = :cid "
                    "ORDER BY created_at ASC"
```

`web/server.py` 的 `/api/history`（line 620-623）SELECT 改為：

```python
                    "SELECT id, question, answer, created_at, feedback, sources, ext_sources, thinking_ms "
                    "FROM research.qa_log "
                    "WHERE answer IS DISTINCT FROM :offtopic "
                    "ORDER BY created_at DESC LIMIT :limit"
```

- [ ] **Step 6: schema 加欄（CREATE ＋ 冪等 ALTER）**

`db/schema.sql` 的 `CREATE TABLE IF NOT EXISTS research.qa_log`（line 77-85），在 `latency_ms int,`（line 83）之後加一行 `thinking_ms      int,`，使表體為：

```sql
CREATE TABLE IF NOT EXISTS research.qa_log (
    id               uuid PRIMARY KEY,
    question         text NOT NULL,
    answer           text,
    cited_report_ids uuid[],                        -- 回答實際引用的報告 id
    filters          jsonb,                         -- 提問時套用的市場/商品/類型等篩選
    latency_ms       int,
    thinking_ms      int,                           -- 思考時間：開始→第一個 token（毫秒）
    created_at       timestamptz NOT NULL DEFAULT now()
);
```

並在該 `CREATE TABLE` 敘述**之後緊接**（與既有 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 慣例一致，供既有 DB 補欄）加入：

```sql
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS thinking_ms int;
```

- [ ] **Step 7: 跑全測試＋語法檢查**

Run: `uv run pytest tests/test_answer.py -v`
Expected: PASS

Run: `uv run black --check app/services/answer.py web/server.py && uv run ruff check app/services/answer.py web/server.py`
Expected: 無錯（如 black 要求格式化，先 `uv run black app/services/answer.py web/server.py` 再重跑）

- [ ] **Step 8: Commit**

```bash
git add db/schema.sql app/services/answer.py web/server.py tests/test_answer.py
git commit -m "$(cat <<'EOF'
feat(ask): qa_log 新增 thinking_ms 欄並於歷史序列化回傳

CREATE 加欄＋冪等 ALTER；history_item 解析第 8 欄（舊列回 None）；
get_conversation 與 /api/history SELECT 帶 thinking_ms，供歷史顯示秒數。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 前端動態思考列（標題狀態機）

**Files:**
- Modify: `web/static/app/ask.js:34-55`（`createTurn` turn 物件加欄）
- Modify: `web/static/app/ask.js:157-186`（`loadConversation` 設 `thinkingMs`）
- Modify: `web/static/app/ask.js:264-350`（`renderProcess`/新 head 助手/`onStatus`/`startGenerating` 區/`clearProcess`/`finishProcess`/`staticProcess`）
- Modify: `web/static/app/ask.js:481`（token fallback 凍結）
- Modify: `web/static/index.html:654` 之後（標題列圖示 CSS）
- Test: 無自動化前端框架 → Playwright 手動驗證（見 Step 8）

**Interfaces:**
- Consumes: Task 1 的 `("status", {"stage":"generating","thinking_ms":N})` 與 `done.thinking_ms`；Task 2 的 `it.thinking_ms`（歷史）。
- Produces: 標題列助手 `setHead(turn, state, label)`、`thinkingLabel(ms) -> string|null`、`flashHead(turn, label)`、`freezeHead(turn, ms)`；turn 新欄 `labelTimer`/`thinkingFrozen`/`thinkingMs`。

- [ ] **Step 1: `createTurn` turn 物件加狀態欄位**

`web/static/app/ask.js`，把 turn 物件字面（line 47-55）改為（在既有欄位上加三欄）：

```js
  const turn = {
    q: question, answer: "", sources: [], extSources: [], qaId: null,
    labelTimer: null, thinkingFrozen: false, thinkingMs: null,
    node,
    answerEl: node.querySelector(".ask-msg-bot"),
    processEl: node.querySelector(".ask-process-host"),
    actionsEl: node.querySelector(".ask-actions"),
    srcEl: node.querySelectorAll(".ask-sources")[0],
    extEl: node.querySelector(".ask-ext-list"),
  };
```

- [ ] **Step 2: `renderProcess` 預設收合＋標題列改圖示與動態標籤**

把 `renderProcess`（line 264-295）整段替換為：

```js
// 建面板：expanded 控制預設展開（即時與歷史皆預設收合）。標題列為動態思考列。web 步驟初始隱藏。
function renderProcess(turn, { expanded = false } = {}) {
  const items = PROC_STEPS.map(s => html`<li class="ask-step" data-step="${s.key}" data-state="pending">
      <span class="ask-step-ico">${raw(PROC_ICO.pending)}</span>
      <span class="ask-step-label">${s.label}</span>
    </li>`).join("");
  turn.processEl.innerHTML = html`<div class="ask-process${expanded ? " open" : ""}">
      <button class="ask-process-head" type="button" aria-expanded="${expanded ? "true" : "false"}">
        ${raw(SVG.chev)}
        <span class="ask-proc-ico">${raw(PROC_ICO.active)}</span>
        <span class="ask-proc-label">正在思考</span>
      </button>
      <ol class="ask-process-steps">${raw(items)}</ol>
    </div>`;
  turn.processEl.querySelector('.ask-step[data-step="web"]').hidden = true;
  const head = turn.processEl.querySelector(".ask-process-head");
  const steps = turn.processEl.querySelector(".ask-process-steps");
  steps.hidden = !expanded;
  steps.setAttribute("aria-hidden", expanded ? "false" : "true");
  head.onclick = () => {
    const box = turn.processEl.querySelector(".ask-process");
    const opening = !box.classList.contains("open");
    if (opening) {
      steps.hidden = false;
      steps.setAttribute("aria-hidden", "false");
      box.classList.add("open");
    } else {
      box.classList.remove("open");
      steps.hidden = true;
      steps.setAttribute("aria-hidden", "true");
    }
    head.setAttribute("aria-expanded", opening ? "true" : "false");
  };
}

// ── 標題列（動態思考列）助手 ──
// state: active 顯示 spinner、done 顯示打勾；label 為標題文字
function setHead(turn, state, label) {
  const head = turn.processEl.querySelector(".ask-process-head");
  if (!head) return;
  head.querySelector(".ask-proc-ico").innerHTML = PROC_ICO[state];
  head.querySelector(".ask-proc-label").textContent = label;
  head.classList.toggle("is-done", state === "done");
}
// 思考耗時標籤；ms 無效回 null（至少顯示 1 秒，避免「0 秒」）
function thinkingLabel(ms) {
  return (typeof ms === "number" && ms >= 0)
    ? `已思考 ${Math.max(1, Math.round(ms / 1000))} 秒`
    : null;
}
// 顯示動作標籤後，短暫（1.8s）還原為「正在思考」（凍結後不再還原）
function flashHead(turn, label) {
  if (turn.thinkingFrozen) return;
  setHead(turn, "active", label);
  clearTimeout(turn.labelTimer);
  turn.labelTimer = setTimeout(() => {
    if (!turn.thinkingFrozen) setHead(turn, "active", "正在思考");
  }, 1800);
}
// 思考結束：凍結為「已思考 X 秒」（無 ms 回退「已思考」）
function freezeHead(turn, ms) {
  turn.thinkingFrozen = true;
  clearTimeout(turn.labelTimer);
  setHead(turn, "done", thinkingLabel(ms) || "已思考");
}
```

- [ ] **Step 3: `onStatus` 加動作閃示與 generating 凍結**

把 `onStatus`（line 308-323）整段替換為：

```js
// status 事件 → 推進步驟＋驅動標題動態標籤（payload 為 {stage,...} 物件）
function onStatus(turn, data) {
  const stage = data && typeof data === "object" ? data.stage : data;
  if (stage === "understanding") {
    setStep(turn, "understand", "active");
    // 標題維持 baseline「正在思考」
  } else if (stage === "retrieved") {
    const label = `找到 ${Number(data.count) || 0} 篇相關研報`;
    setStep(turn, "understand", "done");
    setStep(turn, "retrieved", "done", label);
    setStep(turn, "reading", "active");
    flashHead(turn, label);
  } else if (stage === "reading") {
    setStep(turn, "reading", "active");
    // reading 緊接 retrieved，不另閃標題（避免蓋掉「找到 N 篇」）
  } else if (stage === "searching_web") {
    setStep(turn, "reading", "done");
    setStep(turn, "web", "active");
    flashHead(turn, "搜尋網路補充");
  } else if (stage === "generating") {
    startGenerating(turn);
    freezeHead(turn, data && typeof data === "object" ? data.thinking_ms : null);
  }
}
```

- [ ] **Step 4: `clearProcess`/`finishProcess` 清掉 labelTimer**

把 `clearProcess`（line 306）改為：

```js
function clearProcess(turn) { clearTimeout(turn.labelTimer); turn.processEl.innerHTML = ""; }
```

把 `finishProcess`（line 336-340）改為：

```js
// done：把所有顯示中的步驟標完成（標題已於 generating 凍結）
function finishProcess(turn) {
  clearTimeout(turn.labelTimer);
  turn.processEl.querySelectorAll(".ask-step:not([hidden])").forEach(li => {
    if (li.dataset.state !== "done") setStep(turn, li.dataset.step, "done");
  });
}
```

- [ ] **Step 5: `staticProcess` 用 thinkingMs 設歷史標題**

把 `staticProcess`（line 343-350）整段替換為：

```js
// 歷史重建：以既有 sources/ext_sources/thinking_ms 還原靜態面板（預設收合）
function staticProcess(turn) {
  renderProcess(turn, { expanded: false });
  turn.thinkingFrozen = true;
  // 有 thinking_ms → 「已思考 X 秒」；舊列無值 → 中性「處理過程」
  setHead(turn, "done", thinkingLabel(turn.thinkingMs) || "處理過程");
  setStep(turn, "understand", "done");
  setStep(turn, "retrieved", "done", `找到 ${turn.sources.length} 篇相關研報`);
  setStep(turn, "reading", "done");
  if (turn.extSources.length) setStep(turn, "web", "done");
  setStep(turn, "generate", "done");
}
```

- [ ] **Step 6: `loadConversation` 帶入 thinkingMs；token fallback 凍結**

`loadConversation` 內（line 170-174 區），在 `turn.answer = it.answer || "";` 之後加一行：

```js
      turn.thinkingMs = (typeof it.thinking_ms === "number") ? it.thinking_ms : null;
```

把串流 token 處理（line 481）改為（generating 未到時，第一個 token 作後備凍結）：

```js
        else if (evt.event === "token") { if (!started) { startGenerating(turn); if (!turn.thinkingFrozen) freezeHead(turn, null); started = true; } turn.answer += evt.data; const stick = nearBottom(); paintAnswer(turn, true); if (stick) toBottom(); }
```

- [ ] **Step 7: 標題列圖示 CSS**

`web/static/index.html`，在 `.ask-step-spin { ... }` 規則（line 654-655）之後、`/* 歷史問答 ... */` 註解（line 657）之前，插入：

```css
  .ask-process-head .ask-proc-ico { flex: none; width: 16px; height: 16px; display: grid; place-items: center; }
  .ask-process-head .ask-proc-ico svg { width: 16px; height: 16px; display: block; }
  .ask-process-head .ask-proc-ico .ask-step-spin { width: 14px; height: 14px; }
  .ask-process-head.is-done { color: var(--label-2); }
  .ask-process-head.is-done .ask-proc-ico { color: var(--brand); }
```

- [ ] **Step 8: 手動驗證（Playwright）＋部署本機後端**

先套 schema 並重啟後端（answer.py 與 schema 已改）：

Run: `make schema && sudo systemctl restart report-mark-web.service`
（若本機非 systemd，依既有方式重啟 `make serve` 的 uvicorn；參照 web-502-recovery 慣例。）

用瀏覽器在 `http://localhost:8097` 登入後，於問答模式驗證：

1. **即時**：送出問題 → 標題先顯示「正在思考」（spinner）→ 短暫切「找到 N 篇相關研報」→ 還原「正在思考」→（若有網路搜尋短暫切「搜尋網路補充」）→ 答案開始輸出時凍結「已思考 X 秒」（打勾）。面板預設收合，點標題可展開看 5 步驟（含勾選）。
2. **無脈絡**：問一個查不到的冷門題 → 標題仍凍結「已思考 X 秒」，答案區為無脈絡訊息。
3. **離題**：問離題題 → 面板於拒答時移除（不顯「已思考」），顯示拒答卡。
4. **歷史（新列）**：側欄點剛問完的對話 → 標題「已思考 X 秒」、收合、可展開；秒數與即時時一致。
5. **歷史（舊列）**：點本功能上線前的舊對話 → 標題「處理過程」（無秒數）、可展開步驟，不報錯。
6. Console 無錯誤。

預期：以上 6 點全部符合。

- [ ] **Step 9: Commit**

```bash
git add web/static/app/ask.js web/static/index.html
git commit -m "$(cat <<'EOF'
feat(ask): 處理過程改 ChatGPT 式動態思考列「已思考 XX 秒」

標題列改「狀態圖示＋動態標籤」狀態機：處理中「正在思考」、動作事件
閃示動作標籤後還原、generating 凍結「已思考 X 秒」；面板預設收合。
歷史以 thinking_ms 顯秒數，舊列退回「處理過程」。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**1. Spec coverage**（對照 `2026-06-22-ask-process-steps-design.md` 增補段）

| Spec 要求 | 對應 |
|-----------|------|
| 預設收合、標題動態思考列 | Task 3 Step 2（`renderProcess` 預設 `expanded=false`、head 標籤） |
| 一開始「正在思考」 | Task 3 Step 2（head 初始 label）／Step 3（understanding 不改 head） |
| 有動作顯示動作、再切回「正在思考」 | Task 3 Step 3（`flashHead` retrieved/searching_web）＋ Step 2（`flashHead` 1.8s 還原） |
| 結束凍結「已思考 X 秒」 | Task 3 Step 3（generating→`freezeHead`）＋ Step 6（token 後備凍結） |
| 點開看做過哪些動作 | Task 3 Step 2（步驟 `<ol>` 收合可展開）＋ `setStep` 維持 |
| 秒數＝開始→第一個 token | Task 1 Step 4（`_emit_token` 量 thinking_ms） |
| 單一真實來源、即時＝歷史一致 | Task 1（generating/done 帶 thinking_ms＋寫入）＋ Task 2（歷史讀同欄）＋ Task 3（兩處皆用 `thinkingLabel`） |
| 不挪用 latency_ms、新增 thinking_ms | Task 1 Step 3／Task 2 Step 6 |
| 離題維持 clearProcess、仍記錄 thinking_ms | Task 1 Step 8（離題 done/log 帶 thinking_ms）＋ 既有 notice→`clearProcess`（未改） |
| 無脈絡凍結「已思考 X 秒」 | Task 1 Step 8（generating＋token）＋ Task 3 Step 3 |
| 舊歷史列 NULL → 「處理過程」 | Task 2 Step 3（history_item None）＋ Task 3 Step 5（`|| "處理過程"`） |
| 錯誤／逾時清 labelTimer | Task 3 Step 4（`clearProcess` 清 timer；error/timeout 路徑既有先呼叫 clearProcess） |
| 測試涵蓋 generating／離題／無脈絡 | Task 1 Step 1/6（generating、no-context）；離題既有 `test_off_topic_intent_skips_llm` 仍綠 |

**2. Placeholder scan**：無 TBD/TODO；每個改碼步驟均附完整程式碼與確切路徑行號。

**3. Type consistency**：`thinking_ms`（後端 int|None）↔ `it.thinking_ms`（前端 number|null）↔ `turn.thinkingMs`；助手名稱 `setHead`/`flashHead`/`freezeHead`/`thinkingLabel` 跨 Step 一致；`_emit_token` 僅 Task 1 內部使用；`PROC_ICO.active/done/pending` 沿用既有常數。

**4. 既有測試衝擊**：唯一需改的精確序列斷言為 `test_no_context_emits_retrieved_zero_without_reading`（Task 1 Step 6 已含）。其餘 status 測試用 `assertIn`／索引比較，新增事件與 `thinking_ms` 鍵不破壞。
