# 問答多輪對話（Conversational RAG）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把單輪無狀態問答升級為多輪對話式 RAG——同一對話可連續追問，追問能被正確理解與檢索，對話以 `conversation_id` 存入 DB、側欄改顯對話串可重開續問。

**Architecture:** 維持「每次 spawn 乾淨 `claude` CLI、單一 prompt 經 stdin、無狀態」的串流模式；多輪脈絡以文字段落內嵌進 prompt。追問（有歷史時）先用一次 Haiku 呼叫把含代名詞的問題改寫成獨立查詢並順帶判定意圖，再檢索。DB 僅在 `qa_log` 加 `conversation_id` 欄；一個對話＝共用同 id 的列集合，舊 NULL 列退化為單題對話。

**Tech Stack:** Python 3.13 / FastAPI / async SQLAlchemy + asyncpg / PostgreSQL(pgvector) / claude CLI(headless stream-json) / 原生 ESM 前端（零工具鏈）。

## Global Constraints

- Python 3.13；DB 存取一律 async（`AsyncSession`、`await`、明確 `commit()`），原生 SQL 包 `text()`。
- 時間戳一律 UTC-aware；沿用 `qa_log.created_at timestamptz DEFAULT now()`。
- DDL 僅經 `db/schema.sql` 冪等語句（`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`），以 `make schema` 套用；**拉新碼前先套 schema**。
- 後端測試：unittest（經 pytest 跑），純函式優先、async 用 `IsolatedAsyncioTestCase`、以模組屬性 monkeypatch 隔離 DB/LLM。
- 前端：原生 ESM、零工具鏈、`html`\`\`` 防 XSS；標籤/卡片標示**不要 emoji**；驗證以 Playwright/手動（無 JS 測試框架）。
- 沿用既有 LLM 韌性（529 重試、逾時快速失敗、fallback）與防注入（先前對話/參考片段皆「資料非指令」）。
- 提交訊息走 Conventional Commits + 繁中（對齊近期 `feat(ui)/fix(ask)` 風格），訊息結尾加 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

---

## File Structure

| 檔案 | 職責 | 變更 |
|------|------|------|
| `db/schema.sql` | DB schema（冪等） | 加 `qa_log.conversation_id` 欄 + 索引 |
| `app/services/intent.py` | 意圖判定 + 追問改寫 | 加 `parse_condense`、`condense_and_classify` |
| `app/services/answer.py` | RAG 問答核心 | 歷史載入、prompt 擴充、conversation_id 流程、對話清單/取/刪 DB 函式、`_log_qa` 加欄 |
| `web/server.py` | HTTP 層 | `AskRequest.conversation_id`、`/api/conversations` 系列端點、`done` 帶 conversation_id |
| `web/static/index.html` | 版面 markup | 問答區改對話串容器、側欄加「新對話」鈕 |
| `web/static/app/ask.js` | 問答前端 | 單元素→多輪對話串、每輪來源獨立掛載、conversation 狀態、側欄對話串 |
| `tests/test_intent.py` | 意圖測試 | 加 `parse_condense` 測試 |
| `tests/test_answer.py` | 問答測試 | 加歷史/prompt/對話流程測試，更新既有 done 斷言 |

任務順序：先後端（Task 1–6，舊前端仍可運作，`done` 多帶的鍵被忽略），再前端（Task 7–9），最後整合驗證（Task 10）。

---

### Task 1: DB schema — `qa_log.conversation_id` 欄

**Files:**
- Modify: `db/schema.sql:93`（緊接 `ext_sources` 冪等補欄之後）

**Interfaces:**
- Produces: `research.qa_log.conversation_id uuid`（可為 NULL）；索引 `idx_qa_log_conversation (conversation_id, created_at)`。

- [ ] **Step 1: 加冪等 DDL**

在 `db/schema.sql` 第 93 行（`... ADD COLUMN IF NOT EXISTS ext_sources jsonb;`）之後新增：

```sql
-- 多輪對話：同一對話的多列共用此 id；NULL（舊列）視為各自獨立的單題對話（冪等補欄）
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS conversation_id uuid;
CREATE INDEX IF NOT EXISTS idx_qa_log_conversation
    ON research.qa_log (conversation_id, created_at);
```

- [ ] **Step 2: 套用 schema 並驗證欄位存在**

Run:
```bash
make schema
docker exec -i findb-postgres psql -U postgres -d findb -c "\d research.qa_log" | grep conversation_id
```
Expected: 輸出含 `conversation_id | uuid`。（容器/DB 名以實機為準；`make schema` 內部即 `psql ... < db/schema.sql`。）

- [ ] **Step 3: Commit**

```bash
git add db/schema.sql
git commit -m "$(cat <<'EOF'
feat(ask): qa_log 新增 conversation_id 欄支援多輪對話

冪等補欄 + (conversation_id, created_at) 索引；NULL 舊列退化為單題對話。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: intent.py — 追問改寫 + 意圖合一

**Files:**
- Modify: `app/services/intent.py`（檔尾新增）
- Test: `tests/test_intent.py`

**Interfaces:**
- Consumes: 既有 `parse_intent`、`stream_completion`、`INTENT_MODEL`、`INTENT_TIMEOUT`。
- Produces:
  - `parse_condense(text: str) -> tuple[str | None, bool]` — 解析 `QUERY:`/`INTENT:` 兩行，回 (standalone_query 或 None, in_domain)。
  - `condense_and_classify(history_text: str, question: str, *, model=..., timeout=...) -> tuple[str, bool]` — 一次 Haiku 呼叫，回 (standalone_query, in_domain)，任何錯誤 fail-open 回 `(question, True)`。

- [ ] **Step 1: 寫失敗測試（parse_condense）**

在 `tests/test_intent.py` 的 import 改為同時引入 `parse_condense`，並新增測試類：

```python
from app.services.intent import parse_intent, parse_condense  # noqa: E402


class ParseCondenseTests(unittest.TestCase):
    def test_standard_two_lines(self):
        q, ok = parse_condense("QUERY: 台積電先進封裝的展望\nINTENT: IN")
        self.assertEqual(q, "台積電先進封裝的展望")
        self.assertTrue(ok)

    def test_intent_out(self):
        q, ok = parse_condense("QUERY: 幫我寫詩\nINTENT: OUT")
        self.assertEqual(q, "幫我寫詩")
        self.assertFalse(ok)

    def test_lowercase_keys_and_whitespace(self):
        q, ok = parse_condense("  query:  鴻海營收 \n  intent: in ")
        self.assertEqual(q, "鴻海營收")
        self.assertTrue(ok)

    def test_missing_intent_fails_open_in_domain(self):
        q, ok = parse_condense("QUERY: 只有查詢沒有意圖")
        self.assertEqual(q, "只有查詢沒有意圖")
        self.assertTrue(ok)  # 缺 INTENT → fail-open True

    def test_missing_query_returns_none(self):
        q, ok = parse_condense("INTENT: IN")
        self.assertIsNone(q)
        self.assertTrue(ok)

    def test_garbage_returns_none_and_in_domain(self):
        q, ok = parse_condense("我不知道怎麼改寫")
        self.assertIsNone(q)
        self.assertTrue(ok)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_intent.py::ParseCondenseTests -v`
Expected: FAIL（`ImportError: cannot import name 'parse_condense'`）。

- [ ] **Step 3: 實作 parse_condense 與 condense_and_classify**

在 `app/services/intent.py` 檔尾新增：

```python
CONDENSE_MODEL = os.getenv("ASK_CONDENSE_MODEL", INTENT_MODEL)
CONDENSE_TIMEOUT = float(os.getenv("ASK_CONDENSE_TIMEOUT", "20"))

CONDENSE_SYSTEM_PROMPT = (
    "你是「廷豐研報」投資問答系統的前置處理器。根據『先前對話』，把使用者的"
    "『追問』改寫成一個語意完整、可獨立檢索的問題：補齊代名詞與省略的主語"
    "（例如把「它」「那檔」「上述」還原為具體公司／標的／主題）。同時判斷"
    "改寫後的問題是否屬於『可由投資研究報告回答的金融／市場／個股／總經／期貨提問』。\n"
    "嚴格只輸出兩行，不要任何其他文字或標點說明：\n"
    "QUERY: <改寫後可獨立檢索的完整問題>\n"
    "INTENT: IN 或 OUT"
)


def parse_condense(text: str) -> tuple[str | None, bool]:
    """解析改寫器輸出 → (standalone_query 或 None, in_domain)。

    取 `QUERY:` 行為改寫後查詢（空則 None，由呼叫端退回原問題）；
    `INTENT:` 行交 parse_intent 判定（缺此行 → fail-open True）。
    """
    query: str | None = None
    in_domain = True
    for line in text.splitlines():
        s = line.strip()
        upper = s.upper()
        if upper.startswith("QUERY:"):
            query = s[len("QUERY:"):].strip() or None
        elif upper.startswith("INTENT:"):
            in_domain = parse_intent(s[len("INTENT:"):])
    return query, in_domain


async def condense_and_classify(
    history_text: str,
    question: str,
    *,
    model: str = CONDENSE_MODEL,
    timeout: float = CONDENSE_TIMEOUT,
) -> tuple[str, bool]:
    """一次 Haiku 呼叫：把追問改寫成獨立查詢並判定意圖 → (standalone_query, in_domain)。

    任何錯誤／逾時／空回應／解析不到查詢 → fail-open，回 (原始 question, True)。
    """
    prompt = f"先前對話：\n{history_text}\n\n追問：{question}"
    try:
        parts: list[str] = []
        async for chunk in stream_completion(
            prompt, model=model, system=CONDENSE_SYSTEM_PROMPT, timeout=timeout
        ):
            parts.append(chunk)
        query, in_domain = parse_condense("".join(parts))
        return (query or question, in_domain)
    except Exception:
        return (question, True)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_intent.py -v`
Expected: PASS（含原有 + 新 ParseCondenseTests）。

- [ ] **Step 5: Commit**

```bash
git add app/services/intent.py tests/test_intent.py
git commit -m "$(cat <<'EOF'
feat(ask): 追問改寫+意圖合一 condense_and_classify（Haiku）

有對話歷史時把含代名詞追問改寫成獨立查詢並順帶判定離題；
parse_condense 純函式可測，解析失敗一律 fail-open 退回原問題。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: answer.py — 歷史區塊 + prompt 擴充

**Files:**
- Modify: `app/services/answer.py`（常數區 + `build_user_prompt`，約 `answer.py:225`）
- Test: `tests/test_answer.py`

**Interfaces:**
- Produces:
  - `MAX_HISTORY_TURNS = 3`、`MAX_HISTORY_ANSWER_CHARS = 600`
  - `build_history_block(turns: list[tuple[str, str]], *, max_turns=..., max_answer_chars=...) -> str` — turns 為 [(question, answer), ...] 由舊到新；回「先前對話」文字（空 turns → `""`）。
  - `build_user_prompt(question: str, context: str, history_block: str = "") -> str`（簽章新增第 3 參數，預設 `""` 維持相容）。

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_answer.py` 的 `from app.services.answer import (...)` 區塊加入 `build_history_block`，並新增測試類（接在現有 prompt 測試附近）：

```python
class HistoryBlockTests(unittest.TestCase):
    def test_empty_turns_returns_empty_string(self):
        self.assertEqual(build_history_block([]), "")

    def test_formats_turns_oldest_first(self):
        block = build_history_block([("台積電?", "看好[1]"), ("那聯電?", "中立[1]")])
        self.assertIn("Q1: 台積電?", block)
        self.assertIn("A1: 看好[1]", block)
        self.assertIn("Q2: 那聯電?", block)

    def test_keeps_only_recent_max_turns(self):
        turns = [("q1", "a1"), ("q2", "a2"), ("q3", "a3"), ("q4", "a4")]
        block = build_history_block(turns, max_turns=3)
        self.assertNotIn("q1", block)      # 最舊一輪被丟
        self.assertIn("q4", block)

    def test_truncates_long_answer(self):
        block = build_history_block([("q", "x" * 1000)], max_answer_chars=600)
        self.assertIn("…", block)
        self.assertLess(len(block), 700)


class PromptWithHistoryTests(unittest.TestCase):
    def test_history_block_prepended_when_present(self):
        p = build_user_prompt("新問題", "[1] 報告甲\n內容", "Q1: 舊問\nA1: 舊答")
        self.assertIn("先前對話", p)
        self.assertIn("Q1: 舊問", p)
        self.assertIn("問題：新問題", p)

    def test_no_history_section_when_empty(self):
        p = build_user_prompt("新問題", "[1] 報告甲\n內容", "")
        self.assertNotIn("先前對話", p)
        self.assertIn("問題：新問題", p)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_answer.py::HistoryBlockTests tests/test_answer.py::PromptWithHistoryTests -v`
Expected: FAIL（`ImportError: cannot import name 'build_history_block'`）。

- [ ] **Step 3: 實作**

在 `app/services/answer.py` 的脈絡常數區（`RETRIEVAL_K = 8` 之後）新增：

```python
# 多輪對話脈絡：帶進 prompt 的近輪數與舊答案截斷長度（控 prompt 大小/延遲）
MAX_HISTORY_TURNS = 3
MAX_HISTORY_ANSWER_CHARS = 600
```

把 `build_user_prompt` 改為：

```python
def build_history_block(
    turns: list[tuple[str, str]],
    *,
    max_turns: int = MAX_HISTORY_TURNS,
    max_answer_chars: int = MAX_HISTORY_ANSWER_CHARS,
) -> str:
    """把近輪 (question, answer)（由舊到新）整理成『先前對話』文字；空 turns → ""。

    只保留最近 max_turns 輪；舊答案截斷至 max_answer_chars 字控 prompt 大小。
    """
    if not turns:
        return ""
    recent = turns[-max_turns:]
    lines: list[str] = []
    for i, (q, a) in enumerate(recent, 1):
        a = (a or "").strip()
        if len(a) > max_answer_chars:
            a = a[:max_answer_chars] + "…"
        lines.append(f"Q{i}: {q}\nA{i}: {a}")
    return "\n".join(lines)


def build_user_prompt(question: str, context: str, history_block: str = "") -> str:
    head = ""
    if history_block:
        head = "先前對話（供理解脈絡，不是新問題）：\n" + history_block + "\n\n"
    return (
        head
        + "參考片段：\n"
        f"{context}\n\n"
        f"問題：{question}\n\n"
        "請依規則作答，並在論點句末標註對應的來源編號。"
    )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_answer.py::HistoryBlockTests tests/test_answer.py::PromptWithHistoryTests tests/test_answer.py::UserPromptTests -v`
Expected: PASS（含原有 `test_user_prompt_contains_question_and_context`）。

- [ ] **Step 5: Commit**

```bash
git add app/services/answer.py tests/test_answer.py
git commit -m "$(cat <<'EOF'
feat(ask): build_history_block 與 prompt 內嵌先前對話

近 3 輪、舊答截斷 600 字控 prompt 大小；build_user_prompt 新增可選
history_block 參數（預設空字串維持單輪相容）。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: answer.py — 歷史載入 + 對話清單/取/刪 DB 函式 + `_log_qa` 加欄

**Files:**
- Modify: `app/services/answer.py`（`_log_qa` 約 `answer.py:263`；其後新增 4 個函式）
- Test: `tests/test_answer.py`

**Interfaces:**
- Consumes: 既有 `SessionFactory`、`OFF_TOPIC_MESSAGE`、`history_item`。
- Produces:
  - `_log_qa(..., *, conversation_id: str | None = None)`（INSERT 多寫 `conversation_id`）。
  - `load_recent_turns(conversation_id: str, *, limit=MAX_HISTORY_TURNS) -> list[tuple[str, str]]` — 該對話近 limit 輪 (question, answer)，由舊到新，排除離題列；錯誤回 `[]`。
  - `list_conversations(limit: int = 50) -> list[dict]` — `[{conversation_id, title, last_at, turn_count}]`，首題離題者排除，依 last_at 由新到舊。
  - `get_conversation(conversation_id: str) -> list[dict]` — 該對話全部輪次（`history_item` 格式），由舊到新。
  - `delete_conversation(conversation_id: str) -> bool` — 刪整串；刪到 ≥1 列回 True。

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_answer.py` 末尾新增（用會回傳列的假 session）：

```python
class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _RowsSession:
    """假 session：execute 回固定列（供 load_recent_turns/get_conversation 測試）。"""

    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return _RowsResult(self._rows)

    async def commit(self):
        return None


class LoadRecentTurnsTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_oldest_first(self):
        from app.services import answer as ans
        # DB 以 created_at DESC 回（新→舊）；函式須反轉成舊→新
        rows = [("新問", "新答"), ("舊問", "舊答")]
        orig = ans.SessionFactory
        ans.SessionFactory = lambda: _RowsSession(rows)
        try:
            turns = await ans.load_recent_turns("c1")
        finally:
            ans.SessionFactory = orig
        self.assertEqual(turns, [("舊問", "舊答"), ("新問", "新答")])

    async def test_db_error_returns_empty(self):
        from app.services import answer as ans

        class Boom:
            def __call__(self):
                raise RuntimeError("db down")

        orig = ans.SessionFactory
        ans.SessionFactory = Boom()
        try:
            turns = await ans.load_recent_turns("c1")
        finally:
            ans.SessionFactory = orig
        self.assertEqual(turns, [])


class GetConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_maps_rows_via_history_item(self):
        from app.services import answer as ans
        from datetime import date
        rows = [
            ("id1", "Q1", "A1", date(2026, 6, 1), None, None, None),
            ("id2", "Q2", "A2", date(2026, 6, 2), "like", None, None),
        ]
        orig = ans.SessionFactory
        ans.SessionFactory = lambda: _RowsSession(rows)
        try:
            out = await ans.get_conversation("c1")
        finally:
            ans.SessionFactory = orig
        self.assertEqual([t["question"] for t in out], ["Q1", "Q2"])
        self.assertEqual(out[1]["feedback"], "like")


class DeleteConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_rowcount_zero_is_false(self):
        from app.services import answer as ans

        class Res:
            rowcount = 0

        class Sess:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, *a, **k):
                return Res()

            async def commit(self):
                return None

        orig = ans.SessionFactory
        ans.SessionFactory = lambda: Sess()
        try:
            ok = await ans.delete_conversation("c1")
        finally:
            ans.SessionFactory = orig
        self.assertFalse(ok)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_answer.py::LoadRecentTurnsTests tests/test_answer.py::GetConversationTests tests/test_answer.py::DeleteConversationTests -v`
Expected: FAIL（`AttributeError: module ... has no attribute 'load_recent_turns'`）。

- [ ] **Step 3: 實作**

修改 `_log_qa` 簽章與 INSERT（加 `conversation_id`）：

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
) -> str:
    qa_id = str(uuid.uuid4())
    ext_sources = ext_sources or []
    try:
        async with SessionFactory() as session:
            await session.execute(
                text(
                    "INSERT INTO research.qa_log "
                    "(id, question, answer, cited_report_ids, filters, latency_ms, "
                    "sources, ext_sources, conversation_id) "
                    "VALUES (:id, :q, :a, :cited, :filters, :lat, "
                    ":sources, :ext_sources, :conv)"
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
                },
            )
            await session.commit()
    except Exception:
        pass
    return qa_id
```

在 `_log_qa` 之後新增四個函式：

```python
async def load_recent_turns(
    conversation_id: str, *, limit: int = MAX_HISTORY_TURNS
) -> list[tuple[str, str]]:
    """取該對話最近 limit 輪 (question, answer)，回傳由舊到新；排除離題列。

    以 COALESCE(conversation_id, id) 分組，相容舊 NULL 列（其自身 id 即對話 id）。
    任何 DB 錯誤 → 回 []（fail-open，不擋作答）。
    """
    try:
        async with SessionFactory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT question, answer FROM research.qa_log "
                        "WHERE COALESCE(conversation_id, id) = :cid "
                        "AND answer IS DISTINCT FROM :offtopic "
                        "ORDER BY created_at DESC LIMIT :limit"
                    ),
                    {"cid": conversation_id, "offtopic": OFF_TOPIC_MESSAGE, "limit": limit},
                )
            ).all()
        return [(q, a) for q, a in reversed(rows)]
    except Exception:
        return []


async def list_conversations(limit: int = 50) -> list[dict]:
    """對話串清單：每串 {conversation_id, title, last_at, turn_count}。

    分組鍵 COALESCE(conversation_id, id)；標題取最早一題；首題離題者排除；
    依該串最新時間由新到舊。
    """
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT conv_id, title, last_at, turn_count FROM ("
                    "  SELECT COALESCE(conversation_id, id) AS conv_id,"
                    "         (array_agg(question ORDER BY created_at))[1] AS title,"
                    "         (array_agg(answer ORDER BY created_at))[1] AS first_answer,"
                    "         max(created_at) AS last_at,"
                    "         count(*) AS turn_count"
                    "  FROM research.qa_log"
                    "  GROUP BY COALESCE(conversation_id, id)"
                    ") g WHERE first_answer IS DISTINCT FROM :offtopic "
                    "ORDER BY last_at DESC LIMIT :limit"
                ),
                {"offtopic": OFF_TOPIC_MESSAGE, "limit": limit},
            )
        ).all()
    out: list[dict] = []
    for conv_id, title, last_at, turn_count in rows:
        out.append(
            {
                "conversation_id": str(conv_id),
                "title": title,
                "last_at": last_at.isoformat() if hasattr(last_at, "isoformat") else last_at,
                "turn_count": int(turn_count),
            }
        )
    return out


async def get_conversation(conversation_id: str) -> list[dict]:
    """該對話全部輪次（history_item 格式），由舊到新，供重開重現與續問。"""
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, question, answer, created_at, feedback, sources, ext_sources "
                    "FROM research.qa_log "
                    "WHERE COALESCE(conversation_id, id) = :cid "
                    "ORDER BY created_at ASC"
                ),
                {"cid": conversation_id},
            )
        ).all()
    return [history_item(tuple(r)) for r in rows]


async def delete_conversation(conversation_id: str) -> bool:
    """刪整個對話串；刪到 ≥1 列回 True，查無或 DB 異常回 False。"""
    try:
        async with SessionFactory() as session:
            result = await session.execute(
                text(
                    "DELETE FROM research.qa_log "
                    "WHERE COALESCE(conversation_id, id) = :cid"
                ),
                {"cid": conversation_id},
            )
            await session.commit()
        return getattr(result, "rowcount", 0) > 0
    except Exception:
        return False
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_answer.py::LoadRecentTurnsTests tests/test_answer.py::GetConversationTests tests/test_answer.py::DeleteConversationTests tests/test_answer.py::LogQaSourcesTests -v`
Expected: PASS（含既有 `LogQaSourcesTests`，驗證加欄不破壞既有寫入測試）。

- [ ] **Step 5: Commit**

```bash
git add app/services/answer.py tests/test_answer.py
git commit -m "$(cat <<'EOF'
feat(ask): 對話歷史載入與對話串清單/取/刪 DB 函式

load_recent_turns（近輪脈絡，排除離題）、list/get/delete_conversation
（COALESCE 分組相容舊 NULL 列）；_log_qa 寫入 conversation_id。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: answer.py — `answer_question` 接上 conversation_id 流程

**Files:**
- Modify: `app/services/answer.py`（`answer_question`，約 `answer.py:340-427`；import 區加 `condense_and_classify`）
- Test: `tests/test_answer.py`（更新 `AnswerGateTests`/`AnswerWebTests` patch 集合與 done 斷言，新增續問測試）

**Interfaces:**
- Consumes: Task 2 `condense_and_classify`、Task 3 `build_history_block`/`build_user_prompt`、Task 4 `load_recent_turns`/`_log_qa(conversation_id=...)`。
- Produces: `answer_question(question, *, k=..., filters=None, model=..., conversation_id: str | None = None)`；所有 `done` 事件 payload 含 `conversation_id`；首輪（無傳入 conversation_id）行為不變（意圖與檢索並行），續問改走 condense。

- [ ] **Step 1: 更新既有測試的 patch 集合與斷言（先讓它們反映新契約 → 失敗）**

在 `app/services/answer.py` import 區把 intent 匯入改為：

```python
from app.services.intent import classify_intent, condense_and_classify
```

於 `tests/test_answer.py`：

1. `AnswerGateTests._patch` 內，於 `fake_intent` 之後加假改寫器並擴充 orig/設定：

```python
        async def fake_condense(history_text, question, **k):
            called["condense"] = True
            return (question, in_domain)

        async def fake_load(conversation_id, **k):
            return []

        orig = (
            ans.hybrid_search,
            ans.embed_query_cached,
            ans.stream_completion,
            ans.SessionFactory,
            ans.classify_intent,
            ans.condense_and_classify,
            ans.load_recent_turns,
        )
        ans.hybrid_search = fake_search
        ans.embed_query_cached = fake_embed
        ans.stream_completion = fake_stream
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_intent = fake_intent
        ans.condense_and_classify = fake_condense
        ans.load_recent_turns = fake_load
        return orig
```

2. `AnswerGateTests._restore` 改為解包 7 元素：

```python
    @staticmethod
    def _restore(ans, orig):
        (
            ans.hybrid_search,
            ans.embed_query_cached,
            ans.stream_completion,
            ans.SessionFactory,
            ans.classify_intent,
            ans.condense_and_classify,
            ans.load_recent_turns,
        ) = orig
```

3. `test_off_topic_intent_skips_llm` 的 done 斷言改為（done 現含 conversation_id）：

```python
        self.assertEqual(events[2][0], "done")
        self.assertEqual(events[2][1]["cited"], [])
        self.assertIn("conversation_id", events[2][1])
```

4. `test_on_topic_intent_calls_llm` 末尾加：

```python
        self.assertIn("conversation_id", events[-1][1])
```

5. `AnswerWebTests` 兩個測試的 inline patch（`orig = (...)` 與還原）各加入 `ans.condense_and_classify`、`ans.load_recent_turns`：在 orig tuple 末尾補這兩項，並設 `ans.load_recent_turns = lambda *a, **k: _empty()`（用 async 假函式）。在這兩個方法內各定義：

```python
        async def fake_load(*a, **k):
            return []
        async def fake_condense(*a, **k):
            return (a[1] if len(a) > 1 else "", True)
```

把 orig 改為七元組並對應設定/還原（與上面 `_patch` 同集合）。

- [ ] **Step 2: 新增續問測試（驗證 condense 改寫被用於檢索）**

```python
class FollowUpTests(unittest.IsolatedAsyncioTestCase):
    async def test_followup_uses_condensed_query_for_retrieval(self):
        from app.services import answer as ans

        seen = {}

        async def fake_load(conversation_id, **k):
            return [("台積電前景?", "看好[1]")]

        async def fake_condense(history_text, question, **k):
            return ("台積電 2026 先進封裝 展望", True)

        async def fake_search(session, query, qvec, **k):
            seen["query"] = query
            return [(1, 0.9, make_row("r1", "x.pdf", "TW", "封裝內容。", date(2026, 6, 1), 0.1))]

        def fake_embed(q):
            seen["embed"] = q
            return [0.0]

        async def fake_stream(*a, **k):
            yield "答案[1]"

        async def fake_intent(q, **k):
            raise AssertionError("續問不應呼叫 classify_intent")

        orig = (ans.hybrid_search, ans.embed_query_cached, ans.stream_completion,
                ans.SessionFactory, ans.classify_intent,
                ans.condense_and_classify, ans.load_recent_turns)
        ans.hybrid_search = fake_search
        ans.embed_query_cached = fake_embed
        ans.stream_completion = fake_stream
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_intent = fake_intent
        ans.condense_and_classify = fake_condense
        ans.load_recent_turns = fake_load
        try:
            events = [e async for e in ans.answer_question("那它的封裝呢?", conversation_id="c1")]
        finally:
            (ans.hybrid_search, ans.embed_query_cached, ans.stream_completion,
             ans.SessionFactory, ans.classify_intent,
             ans.condense_and_classify, ans.load_recent_turns) = orig

        self.assertEqual(seen["query"], "台積電 2026 先進封裝 展望")  # 用改寫後查詢檢索
        self.assertEqual(seen["embed"], "台積電 2026 先進封裝 展望")
        self.assertEqual(events[-1][1]["conversation_id"], "c1")     # 沿用傳入對話 id
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `uv run pytest tests/test_answer.py::AnswerGateTests tests/test_answer.py::FollowUpTests -v`
Expected: FAIL（`answer_question` 尚未支援 `conversation_id` / 未呼叫 condense；done 缺 conversation_id）。

- [ ] **Step 4: 重寫 `answer_question`**

把 `app/services/answer.py` 的 `answer_question` 整段替換為：

```python
async def answer_question(
    question: str,
    *,
    k: int = RETRIEVAL_K,
    filters: dict | None = None,
    model: str = DEFAULT_MODEL,
    conversation_id: str | None = None,
) -> AsyncIterator[tuple[str, object]]:
    """產生 ("sources"|"status"|"token"|"notice"|"ext_sources"|"done", payload) 事件序列。

    首輪（未帶 conversation_id）：意圖判定與檢索並行（省延遲）。
    續問（帶 conversation_id）：先載近輪歷史，一次 Haiku 改寫追問為獨立查詢並判定意圖，
    再以改寫後查詢檢索；先前對話內嵌進 prompt。所有 done 事件回傳 conversation_id。
    """
    filters = filters or {}
    started = time.monotonic()
    conv_id = conversation_id or str(uuid.uuid4())

    # 僅「續問」才載歷史；首輪無歷史，維持並行意圖判定
    turns = await load_recent_turns(conv_id) if conversation_id else []
    history_block = build_history_block(turns)

    if turns:
        standalone_query, in_domain = await condense_and_classify(history_block, question)
        qvec = await asyncio.to_thread(embed_query_cached, standalone_query)
        async with SessionFactory() as session:  # 短連線：檢索完即釋放
            scored = await hybrid_search(session, standalone_query, qvec, k=k, **filters)
    else:
        intent_task = asyncio.create_task(classify_intent(question))
        try:
            qvec = await asyncio.to_thread(embed_query_cached, question)
            async with SessionFactory() as session:
                scored = await hybrid_search(session, question, qvec, k=k, **filters)
            in_domain = await intent_task
        except BaseException:
            intent_task.cancel()
            raise

    if not in_domain:  # 離題：拒答、不跑主 LLM
        yield ("sources", [])
        yield ("notice", OFF_TOPIC_MESSAGE)
        await _log_qa(
            question, OFF_TOPIC_MESSAGE, [], filters,
            int((time.monotonic() - started) * 1000), [], [],
            conversation_id=conv_id,
        )
        yield ("done", {"cited": [], "conversation_id": conv_id})
        return

    sources, context = build_context(scored)
    yield ("sources", [asdict(s) for s in sources])

    if not context:
        yield ("token", NO_CONTEXT_MESSAGE)
        qa_id = await _log_qa(
            question, NO_CONTEXT_MESSAGE, [], filters,
            int((time.monotonic() - started) * 1000), [], [],
            conversation_id=conv_id,
        )
        yield ("done", {"cited": [], "qa_id": qa_id, "conversation_id": conv_id})
        return

    user_prompt = build_user_prompt(question, context, history_block)
    raw_parts: list[str] = []
    buf = ""
    hold = len(EXT_SENTINEL)
    sentinel_found = False
    searching_sent = False
    async for chunk in stream_completion(
        user_prompt, model=model, system=SYSTEM_PROMPT, allow_web=ASK_ENABLE_WEB
    ):
        if chunk == SEARCH_EVENT:
            if not searching_sent:
                searching_sent = True
                yield ("status", "searching_web")
            continue
        raw_parts.append(chunk)
        if sentinel_found:
            continue
        buf += chunk
        idx = buf.find(EXT_SENTINEL)
        if idx != -1:
            if buf[:idx]:
                yield ("token", buf[:idx])
            sentinel_found = True
            buf = ""
        elif len(buf) > hold:
            yield ("token", buf[:-hold])
            buf = buf[-hold:]
    if not sentinel_found and buf:
        yield ("token", buf)

    raw = "".join(raw_parts)
    body, ext_sources = split_external_sources(raw)
    cited = cited_report_ids(body, sources)
    yield ("ext_sources", ext_sources)
    qa_id = await _log_qa(
        question, body, cited, filters,
        int((time.monotonic() - started) * 1000),
        [asdict(s) for s in sources], ext_sources,
        conversation_id=conv_id,
    )
    yield ("done", {"cited": cited, "qa_id": qa_id, "conversation_id": conv_id})
```

- [ ] **Step 5: 跑測試確認通過（含全 answer/intent 套件）**

Run: `uv run pytest tests/test_answer.py tests/test_intent.py -v`
Expected: PASS（全綠，含更新後的 Gate/Web 測試與新 FollowUpTests）。

- [ ] **Step 6: Commit**

```bash
git add app/services/answer.py tests/test_answer.py
git commit -m "$(cat <<'EOF'
feat(ask): answer_question 支援多輪對話脈絡

續問載近輪歷史並用 condense_and_classify 改寫查詢後檢索、prompt 內嵌
先前對話；首輪維持意圖/檢索並行。所有 done 事件回傳 conversation_id。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: server.py — `AskRequest.conversation_id` + 對話端點

**Files:**
- Modify: `web/server.py`（`AskRequest` 約 `server.py:164`；`/api/ask` 約 `server.py:557`；import 區；history 端點附近新增對話端點）

**Interfaces:**
- Consumes: Task 4/5 `answer_question(..., conversation_id=...)`、`list_conversations`、`get_conversation`、`delete_conversation`。
- Produces:
  - `AskRequest.conversation_id: str | None = None`
  - `GET /api/conversations?limit=` → `list[dict]`
  - `GET /api/conversations/{conversation_id}` → `list[dict]`（輪次）
  - `DELETE /api/conversations/{conversation_id}` → `{"ok": bool}`
  - `POST /api/conversations/{conversation_id}/delete` → `{"ok": bool}`（DELETE 不穩時前端回退）

- [ ] **Step 1: 擴充 import 與 AskRequest**

`web/server.py` 的 `from app.services.answer import (...)` 區塊加入：

```python
    delete_conversation,
    get_conversation,
    list_conversations,
```

`AskRequest` 加欄位（置於 `question` 之後）：

```python
class AskRequest(BaseModel):
    question: str
    conversation_id: str | None = None
    market: str | None = None
```
（其餘欄位不變。）

- [ ] **Step 2: 把 conversation_id 傳入 answer_question**

`/api/ask` 的 `gen()` 內呼叫改為：

```python
                async for event, payload in answer_question(
                    question, k=k, filters=filters, conversation_id=req.conversation_id
                ):
```

- [ ] **Step 3: 新增對話端點**

在 `@app.post("/api/history/{qa_id}/delete")` 之後新增：

```python
@app.get("/api/conversations")
async def conversations(limit: int = Query(50, ge=1, le=200)):
    """對話串清單（首題非離題者）；唯讀，供側欄。"""
    return await list_conversations(limit)


@app.get("/api/conversations/{conversation_id}")
async def conversation_detail(conversation_id: str):
    """單一對話全部輪次（由舊到新），供重開重現與續問。"""
    return await get_conversation(conversation_id)


@app.delete("/api/conversations/{conversation_id}")
async def conversation_delete(conversation_id: str):
    """刪整個對話串。回 {"ok": bool}。"""
    ok = await delete_conversation(conversation_id)
    return {"ok": ok}


@app.post("/api/conversations/{conversation_id}/delete")
async def conversation_delete_post(conversation_id: str):
    """相容性刪除路由（某些代理/邊緣對 DELETE 不穩時前端回退）。"""
    ok = await delete_conversation(conversation_id)
    return {"ok": ok}
```

- [ ] **Step 4: 語法檢查 + 後端全測 + 手動端點驗證**

Run:
```bash
uv run python -c "import ast; ast.parse(open('web/server.py').read()); print('ok')"
uv run pytest tests/ -q
```
Expected: `ok`；pytest 全綠。

啟動服務後（沿用既有部署方式）以登入 cookie 手動驗證（範例，cookie 名與值以實機為準）：
```bash
# 提問取得 conversation_id（看 done 事件）
curl -sN -X POST localhost:8097/api/ask -H 'Content-Type: application/json' \
  -b "$COOKIE" -d '{"question":"台積電先進封裝"}' | grep -E "conversation_id" | tail -1
# 對話清單
curl -s -b "$COOKIE" localhost:8097/api/conversations | head -c 400
```
Expected: `done` 事件含 `conversation_id`；`/api/conversations` 回 JSON 陣列含該串。

- [ ] **Step 5: Commit**

```bash
git add web/server.py
git commit -m "$(cat <<'EOF'
feat(ask): /api/ask 帶 conversation_id 並新增對話串端點

AskRequest 新增 conversation_id 並透傳 answer_question；新增
GET /api/conversations、GET/DELETE /api/conversations/{id}（含 POST 刪除回退）。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: 前端 — 對話串版面 + 每輪來源獨立掛載

**Files:**
- Modify: `web/static/index.html`（問答區 markup 約 `index.html:876-880`；CSS 約 `index.html:740` 附近）
- Modify: `web/static/app/ask.js`（核心重構）

**Interfaces:**
- Produces（ask.js 模組內）：
  - 模組狀態 `turns: TurnObj[]`、移除單一 `sources`/`extSources` 模組變數。
  - `createTurn(question) -> TurnObj`，`TurnObj = { q, answer, sources, extSources, qaId, node, answerEl, actionsEl, srcEl, extEl }`。
  - paint/動作函式改吃 `turn`：`paintAnswer(turn, streaming)`、`paintSources(turn)`、`paintExtSources(turn)`、`thinking(turn)`、`searchingWeb(turn)`、`paintNotice(turn, msg)`、`resetActions(turn)`、`paintActions(turn)`。
- Consumes（Task 8/9）：`askQuestion()` 用 `createTurn` 產生當前輪並串流寫入。

- [ ] **Step 1: 改 markup——對話串容器（移除單元素，保留 askEmpty）**

把 `web/static/index.html` 的 `.ask-thread-inner` 內（`index.html:866-881`）改為：

```html
            <div class="ask-thread-inner" id="askThreadInner">
              <div class="ask-empty" id="askEmpty">
                <div class="ask-empty-title">向廷豐智能體提問</div>
                <div class="ask-empty-sub">輸入想問的問題，回答會引用並可點開原始報告。</div>
                <div class="ask-examples" id="askExamples">
                  <button class="ex" type="button">AI 伺服器的最新發展</button>
                  <button class="ex" type="button">台積電先進封裝</button>
                  <button class="ex" type="button">美國升息的影響</button>
                </div>
              </div>
            </div>
```
（移除 `#askQuestion`/`#askAnswer`/`#askActions`/`#askSources`/`#askExtSources` 五個靜態元素；輪次改由 JS 動態 append。）

- [ ] **Step 2: 加每輪間距 CSS**

在 `index.html` 的 `#askPanel.landing ...` 區塊（約 `index.html:740`）之前新增：

```css
  .ask-turn { display: flex; flex-direction: column; }
  .ask-turn + .ask-turn { margin-top: 18px; }
```
（`.ask-msg-user`/`.ask-msg-bot`/`.ask-actions`/`.ask-sources` 既有樣式沿用，輪次內元素直接套用同 class。）

- [ ] **Step 3: 重構 ask.js——turn 模型與 paint 函式**

把 `web/static/app/ask.js` 開頭模組變數（`let sources = []; let extSources = [];`）改為：

```js
let turns = [];           // 已渲染輪次（每輪自帶來源，避免 [n] 點到別輪報告）
let conversationId = null; // 當前對話 id（Task 8 由 done 事件取回）
let currentAskCtrl = null;
```

新增 `createTurn` 與重構後的 paint 函式（取代原本對 `#askQuestion`/`#askAnswer` 等的單元素版本）：

```js
function createTurn(question) {
  const inner = $("#askThreadInner");
  const node = document.createElement("div");
  node.className = "ask-turn";
  node.innerHTML = html`
    <div class="ask-msg-user"></div>
    <div class="ask-msg-bot" aria-live="polite"></div>
    <div class="ask-actions" hidden></div>
    <div class="ask-sources"></div>
    <div class="ask-sources ask-ext-list"></div>`;
  node.querySelector(".ask-msg-user").textContent = question;
  inner.appendChild(node);
  const turn = {
    q: question, answer: "", sources: [], extSources: [], qaId: null,
    node,
    answerEl: node.querySelector(".ask-msg-bot"),
    actionsEl: node.querySelector(".ask-actions"),
    srcEl: node.querySelectorAll(".ask-sources")[0],
    extEl: node.querySelector(".ask-ext-list"),
  };
  // [n] 引用點擊：對應「本輪」來源
  turn.answerEl.addEventListener("click", e => openCiteIn(turn, e.target));
  turn.answerEl.addEventListener("keydown", e => {
    if ((e.key === "Enter" || e.key === " ") && e.target.classList?.contains("cite")) {
      e.preventDefault(); openCiteIn(turn, e.target);
    }
  });
  turns.push(turn);
  return turn;
}

function openCiteIn(turn, target) {
  const a = target.closest && target.closest(".cite");
  if (!a) return;
  const s = turn.sources.find(x => x.n === parseInt(a.dataset.n, 10));
  if (s) openFull(s.report_id);
}

function paintAnswer(turn, streaming) {
  turn.answerEl.innerHTML =
    renderMarkdown(turn.answer, turn.sources.length) + (streaming ? '<span class="ask-caret"></span>' : "");
}

function paintSources(turn) {
  const el = turn.srcEl;
  const srcs = turn.sources;
  if (!srcs.length) { el.innerHTML = ""; return; }
  const rows = srcs.map(s => html`<button class="ask-src" type="button" data-id="${s.report_id}">
      <span class="ask-src-n">${String(s.n)}</span>
      <span class="badge" style="background:${mColor(s.market)}">${mLabel(s.market)}</span>
      <span class="ask-src-main">
        <span class="ask-src-name">${s.file_name}</span>
        ${s.report_date ? html`<span class="ask-src-date">${fmtDate(s.report_date)}</span>` : raw("")}
      </span>
    </button>`).join("");
  el.innerHTML = html`<div class="ask-src-title">引用來源</div>` + rows;
  el.querySelectorAll(".ask-src").forEach(b => b.onclick = () => openFull(b.dataset.id));
}

function paintExtSources(turn) {
  const el = turn.extEl;
  const list = (turn.extSources || []).filter(s => safeHttp(s.url));
  if (!list.length) { el.innerHTML = ""; return; }
  el.innerHTML = html`<div class="ask-src-title">外部參考</div>` + list.map(s => html`
    <a class="ask-ext" href="${s.url}" target="_blank" rel="noopener noreferrer">
      <span class="ask-ext-badge">網路</span>
      <span class="ask-ext-main">
        <span class="ask-ext-title">${s.title || s.url}</span>
        <span class="ask-ext-url">${domainOf(s.url)}</span>
      </span>
      <span class="ask-ext-go">${raw(SVG.ext)}</span>
    </a>`).join("");
}

function thinking(turn) {
  turn.answerEl.innerHTML =
    `<span class="ask-thinking"><span class="spin"></span>檢索研報並思考中…</span>`;
}

function searchingWeb(turn) {
  const el = turn.answerEl;
  if (el.querySelector(".ask-thinking") && !el.querySelector(".ask-searching-inline")) {
    el.innerHTML = `<span class="ask-thinking"><span class="spin"></span>正在搜尋網路補充最新資料…</span>`;
  } else if (!el.querySelector(".ask-searching-inline")) {
    el.insertAdjacentHTML("beforeend",
      `<span class="ask-thinking ask-searching-inline"><span class="spin"></span>正在搜尋網路補充最新資料…</span>`);
    if (nearBottom()) toBottom();
  }
}

function paintNotice(turn, msg) {
  turn.answerEl.innerHTML = html`<div class="ask-notice">
      <span class="ask-notice-icon" aria-hidden="true">i</span>
      <div class="ask-notice-main">
        <div class="ask-notice-title">無法回答此問題</div>
        <div class="ask-notice-body">${msg}</div>
      </div>
    </div>`;
}

function resetActions(turn) {
  turn.actionsEl.innerHTML = ""; turn.actionsEl.hidden = true;
  turn.srcEl.classList.remove("open");
  turn.extEl.classList.remove("open"); turn.extEl.innerHTML = "";
}
```

把 `paintActions`/`onAction`/`toggleSources`/`toggleExt` 改為以 turn 為作用域：

```js
function paintActions(turn) {
  const el = turn.actionsEl;
  const srcCount = turn.sources.length, extCount = turn.extSources.length;
  const srcBtn = srcCount
    ? html`<button class="ask-act ask-act-src" data-act="sources" type="button"
        aria-expanded="false">${raw(SVG.chev)}資料來源 <span class="ask-act-count">${String(srcCount)}</span></button>`
    : raw("");
  const extBtn = extCount
    ? html`<button class="ask-act ask-act-extsrc" data-act="ext" type="button"
        aria-expanded="false">${raw(SVG.chev)}外部參考 <span class="ask-act-count">${String(extCount)}</span></button>`
    : raw("");
  el.innerHTML = html`<button class="ask-act" data-act="like" type="button" title="有幫助" aria-label="讚">${raw(SVG.up)}</button>
    <button class="ask-act" data-act="dislike" type="button" title="沒幫助" aria-label="倒讚">${raw(SVG.down)}</button>
    <button class="ask-act" data-act="copy" type="button" title="複製回答" aria-label="複製回答">${raw(SVG.copy)}</button>
    ${srcBtn}${extBtn}`;
  el.hidden = false;
  el.querySelectorAll(".ask-act").forEach(b => { b.onclick = () => onAction(turn, b); });
}

function onAction(turn, btn) {
  const act = btn.dataset.act;
  if (act === "like" || act === "dislike") sendFeedback(turn.qaId, act, turn.actionsEl, btn);
  else if (act === "copy") copyText(turn.answer, btn);
  else if (act === "sources") { const open = turn.srcEl.classList.toggle("open"); btn.setAttribute("aria-expanded", open ? "true" : "false"); btn.classList.toggle("on", open); if (open && nearBottom()) toBottom(); }
  else if (act === "ext") { const open = turn.extEl.classList.toggle("open"); btn.setAttribute("aria-expanded", open ? "true" : "false"); btn.classList.toggle("on", open); if (open && nearBottom()) toBottom(); }
}
```

從 `initAsk` 移除對單一 `#askAnswer` 綁定的 cite 點擊區塊（已改為 `createTurn` 內每輪綁定）。`fail(msg)` 改為 `fail(turn, msg) { turn.answerEl.textContent = msg; }`。

- [ ] **Step 4: 改寫 `askQuestion` 用 turn（暫不接 conversation；Task 8 補）**

把 `askQuestion` 主體改為建立新 turn 並串流寫入該 turn：

```js
export async function askQuestion() {
  const input = $("#askInput");
  const q = input.value.trim();
  if (!q) return;
  cancelActiveAsk({ bumpReq: true });
  $("#askPanel").classList.remove("landing");
  $("#askEmpty").hidden = true;
  const my = state.askReq;
  $("#askGo").disabled = true;
  input.value = ""; autoGrow(input);
  const turn = createTurn(q);
  thinking(turn);
  toBottom();
  let started = false, notice = false;
  currentAskCtrl = new AbortController();
  try {
    const resp = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }),   // Task 8 會補 conversation_id
      signal: currentAskCtrl.signal,
    });
    if (resp.status === 401) { window.location.href = "/login"; return; }
    if (!resp.ok || !resp.body) throw new Error("bad response");
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      if (my !== state.askReq) { reader.cancel(); return; }
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const evt = parseFrame(buf.slice(0, idx));
        buf = buf.slice(idx + 2);
        if (!evt) continue;
        if (evt.event === "sources") { turn.sources = evt.data || []; paintSources(turn); }
        else if (evt.event === "status") { if (evt.data === "searching_web") searchingWeb(turn); }
        else if (evt.event === "ext_sources") { turn.extSources = (evt.data || []).filter(s => s && safeHttp(s.url)); paintExtSources(turn); }
        else if (evt.event === "token") { started = true; turn.answer += evt.data; const stick = nearBottom(); paintAnswer(turn, true); if (stick) toBottom(); }
        else if (evt.event === "notice") { notice = true; started = true; paintNotice(turn, evt.data); toBottom(); }
        else if (evt.event === "done") { turn.qaId = (evt.data && evt.data.qa_id) || null; if (evt.data && evt.data.conversation_id) conversationId = evt.data.conversation_id; }
        else if (evt.event === "error") { fail(turn, "問答服務發生錯誤，請稍後再試。"); return; }
      }
    }
    if (my === state.askReq) {
      if (!notice) paintAnswer(turn, false);
      if (!started) fail(turn, "沒有取得回答，請稍後再試。");
      else if (!notice) paintActions(turn);
      loadAskHistory();
    }
  } catch (e) {
    if (my === state.askReq) fail(turn, "查詢逾時或失敗，請稍後再試。");
  } finally {
    if (my === state.askReq) currentAskCtrl = null;
    if (my === state.askReq) $("#askGo").disabled = false;
  }
}
```

同步調整 `loadHistoryItem`（暫時相容：清掉現有 turns、建一個 turn 灌入歷史內容）——此函式於 Task 9 會由對話載入取代，這裡先讓它不引用已移除的單元素：

```js
function loadHistoryItem(it) {
  cancelActiveAsk({ bumpReq: true });
  $("#askPanel").classList.remove("landing");
  $("#askEmpty").hidden = true;
  $("#askThreadInner").querySelectorAll(".ask-turn").forEach(n => n.remove());
  turns = [];
  const turn = createTurn(it.question);
  turn.sources = it.sources || [];
  turn.extSources = it.ext_sources || [];
  turn.qaId = it.id;
  turn.answer = it.answer || "";
  paintSources(turn); paintExtSources(turn); paintAnswer(turn, false);
  paintActions(turn);
  if (it.feedback) {
    const sel = it.feedback === "like" ? "[data-act='like']" : "[data-act='dislike']";
    const btn = turn.actionsEl.querySelector(sel);
    if (btn) btn.classList.add("on");
  }
  toBottom();
}
```

- [ ] **Step 5: 語法檢查 + Playwright 驗證（多輪畫面 + [n] 對應正確）**

Run（語法）:
```bash
node --check web/static/app/ask.js && echo "ask.js ok"
```
Expected: `ask.js ok`。

用 playwright-skill 登入 → 切到問答模式 → 連問兩題 → 斷言：
1. 兩組「問泡＋答泡」同時可見（DOM 有 2 個 `.ask-turn`）。
2. 第一題答案內 `[1]` 點擊開啟的報告，與第一題來源清單第 1 筆一致（非第二題）。
3. 第二題答案正常串流、動作列出現。

Expected: 三項皆通過。

- [ ] **Step 6: Commit**

```bash
git add web/static/index.html web/static/app/ask.js
git commit -m "$(cat <<'EOF'
feat(ask): 問答改可堆疊對話串、每輪來源獨立掛載

單一問答元素改為動態 append 的多輪 turn；[n] 引用與動作列/來源切換
綁定該輪自身來源，避免點舊輪 [n] 開到別篇報告。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: 前端 — 接上 conversation_id（同串續問）

**Files:**
- Modify: `web/static/app/ask.js`（`askQuestion` 送出 body）

**Interfaces:**
- Consumes: 模組變數 `conversationId`（Task 7 已於 done 事件回填）。
- Produces: 續問時 `/api/ask` body 帶 `conversation_id`；不覆蓋既有 turns（append 行為已具備）。

- [ ] **Step 1: 送出帶 conversation_id**

把 `askQuestion` 內的 fetch body 改為：

```js
      body: JSON.stringify(conversationId ? { question: q, conversation_id: conversationId } : { question: q }),
```

（首輪 `conversationId` 為 null → 不帶；後端鑄新 id，done 回填 `conversationId`；其後同串續問皆帶上。）

- [ ] **Step 2: Playwright 驗證（脈絡延續）**

用 playwright-skill：問「台積電先進封裝的重點」→ 待答完 → 追問「那它的競爭對手呢？」。在 Network 面板斷言第二次 `/api/ask` 的 request body 含 `conversation_id`，且其值等於第一次 `done` 回傳的 id。回答內容應延續台積電脈絡（人工肉眼確認，非硬斷言）。

Expected: 第二次請求帶相同 `conversation_id`。

- [ ] **Step 3: Commit**

```bash
git add web/static/app/ask.js
git commit -m "$(cat <<'EOF'
feat(ask): 續問帶 conversation_id 維持同一對話脈絡

首輪由後端鑄 id 並於 done 回填，其後同串續問皆帶上同一
conversation_id；追問即在該對話內延續。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: 前端 — 側欄對話串清單 + 新對話 + 刪整串

**Files:**
- Modify: `web/static/index.html`（側欄 `ask-hist-side` 約 `index.html:806-809`；CSS）
- Modify: `web/static/app/ask.js`（`loadAskHistory`/`renderHistory`/刪除 → 對話版；新增 `newConversation`、`loadConversation`）

**Interfaces:**
- Consumes: `GET /api/conversations`、`GET /api/conversations/{id}`、`DELETE`/POST 刪除端點。
- Produces:
  - `loadAskHistory()`（改打 `/api/conversations`，渲染對話串清單）。
  - `loadConversation(conversationId)`（載入全部輪次重現並接上續問）。
  - `newConversation()`（清空畫面、`conversationId=null`、回 landing）。

- [ ] **Step 1: 側欄 markup——加「新對話」鈕**

把 `web/static/index.html:806-809` 的 `ask-hist-side` 改為：

```html
      <div class="ask-hist-side" id="askHistSide">
        <div class="ask-hist-head">
          <span class="filters-label">歷史對話</span>
          <button class="ask-new" id="askNew" type="button" title="開新對話" aria-label="開新對話">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            新對話
          </button>
        </div>
        <div class="ask-hist-list" id="askHistList"></div>
      </div>
```

加 CSS（接在 `.ask-hist-side` 規則附近，約 `index.html:633`）：

```css
  .ask-hist-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .ask-new { display: inline-flex; align-items: center; gap: 4px; font-size: 13px; color: var(--brand);
    background: var(--brand-soft, #ae74151f); border: none; border-radius: 8px; padding: 5px 9px; cursor: pointer;
    transition: background .2s; }
  .ask-new:hover { background: #ae74152e; }
  .ask-new svg { width: 15px; height: 15px; }
  .ask-hist-item.active { background: #78788022; }
```

- [ ] **Step 2: 重寫側欄載入/渲染為對話串**

把 `loadAskHistory`/`renderHistory` 改為：

```js
export async function loadAskHistory() {
  const list = $("#askHistList");
  if (!list) return;
  if (!list.children.length) list.innerHTML = `<div class="ask-hist-empty">載入中…</div>`;
  try {
    const resp = await fetch("/api/conversations?limit=50");
    if (resp.status === 401) { window.location.href = "/login"; return; }
    if (!resp.ok) throw new Error("bad");
    renderHistory(await resp.json());
  } catch (e) {
    if (!list.children.length || list.querySelector(".ask-hist-empty"))
      list.innerHTML = `<div class="ask-hist-empty">載入失敗，請稍後再試。</div>`;
  }
}

function renderHistory(items) {
  const list = $("#askHistList");
  if (!items.length) { list.innerHTML = `<div class="ask-hist-empty">尚無歷史對話</div>`; return; }
  list.innerHTML = items.map(it => html`<div class="ask-hist-item" data-id="${it.conversation_id}">
      <button class="ask-hist-open" type="button" data-id="${it.conversation_id}" title="${it.title}">
        <span class="ask-hist-q">${it.title}</span>
      </button>
      <button class="ask-hist-del" type="button" data-id="${it.conversation_id}" aria-label="刪除此對話" title="刪除此對話">${raw(SVG.trash)}</button>
    </div>`).join("");
  list.querySelectorAll(".ask-hist-open").forEach(b =>
    b.onclick = () => loadConversation(b.dataset.id));
  list.querySelectorAll(".ask-hist-del").forEach(b =>
    b.onclick = () => deleteConversationItem(b.dataset.id, b));
  markActive();
}

function markActive() {
  document.querySelectorAll("#askHistList .ask-hist-item").forEach(el =>
    el.classList.toggle("active", el.dataset.id === conversationId));
}
```

- [ ] **Step 3: 載入整個對話、新對話、刪整串**

新增 / 取代：

```js
async function loadConversation(id) {
  cancelActiveAsk({ bumpReq: true });
  try {
    const resp = await fetch(`/api/conversations/${encodeURIComponent(id)}`);
    if (resp.status === 401) { window.location.href = "/login"; return; }
    if (!resp.ok) throw new Error("bad");
    const items = await resp.json();
    $("#askPanel").classList.remove("landing");
    $("#askEmpty").hidden = true;
    $("#askThreadInner").querySelectorAll(".ask-turn").forEach(n => n.remove());
    turns = [];
    conversationId = id;   // 接上此對話，輸入框續問即同串
    for (const it of items) {
      const turn = createTurn(it.question);
      turn.sources = it.sources || [];
      turn.extSources = it.ext_sources || [];
      turn.qaId = it.id;
      turn.answer = it.answer || "";
      paintSources(turn); paintExtSources(turn); paintAnswer(turn, false); paintActions(turn);
      if (it.feedback) {
        const sel = it.feedback === "like" ? "[data-act='like']" : "[data-act='dislike']";
        const btn = turn.actionsEl.querySelector(sel);
        if (btn) btn.classList.add("on");
      }
    }
    markActive();
    toBottom();
  } catch (e) { /* 載入失敗不破壞現況 */ }
}

function newConversation() {
  cancelActiveAsk({ bumpReq: true });
  conversationId = null;
  turns = [];
  $("#askThreadInner").querySelectorAll(".ask-turn").forEach(n => n.remove());
  $("#askEmpty").hidden = false;
  $("#askPanel").classList.add("landing");
  markActive();
  $("#askInput").focus();
}

async function deleteConversationItem(id, btn) {
  if (!id || btn.disabled) return;
  const ok = await confirmDialog({
    title: "刪除此對話？",
    body: "將永久移除整個對話串，無法復原。",
    confirmLabel: "刪除",
  });
  if (!ok) return;
  btn.disabled = true;
  try {
    const path = `/api/conversations/${encodeURIComponent(id)}`;
    let resp = await fetch(path, { method: "DELETE" });
    if (resp.status === 404 || resp.status === 405) resp = await fetch(`${path}/delete`, { method: "POST" });
    if (resp.status === 401) { window.location.href = "/login"; return; }
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || !data.ok) throw new Error("bad");
    btn.closest(".ask-hist-item")?.remove();
    if (id === conversationId) newConversation();    // 刪到當前對話 → 回到新對話
    const list = $("#askHistList");
    if (list && !list.children.length) list.innerHTML = `<div class="ask-hist-empty">尚無歷史對話</div>`;
  } catch (e) { btn.disabled = false; }
}
```

刪除舊的 `deleteHistoryItem`、`loadHistoryItem`（已由 `deleteConversationItem`/`loadConversation` 取代）。在 `initAsk` 末尾綁定新對話鈕：

```js
  const nb = $("#askNew");
  if (nb) nb.onclick = () => newConversation();
```

提問成功後維持 `loadAskHistory()` 刷新清單，並在 `askQuestion` 的 `done` 回填 `conversationId` 後呼叫 `markActive()`（於 `loadAskHistory()` 之後）。

- [ ] **Step 4: 語法檢查 + Playwright 全流程驗證**

Run: `node --check web/static/app/ask.js && echo ok`
Expected: `ok`。

用 playwright-skill 驗證：
1. 連問兩題 → 側欄出現「一個對話串」（非兩列）。
2. 按「新對話」→ 畫面清空回 landing、`conversationId` 清掉 → 問一題 → 側欄變兩串。
3. 點側欄第一串 → 主畫面重現其全部輪次、該列 `.active` 高亮 → 再追問 → append 到同串、清單仍同一串。
4. 刪除某串 → 該列消失；若刪的是當前對話 → 畫面回 landing。

Expected: 四項皆通過。

- [ ] **Step 5: Commit**

```bash
git add web/static/index.html web/static/app/ask.js
git commit -m "$(cat <<'EOF'
feat(ask): 側欄改對話串清單，支援新對話與重開續問

側欄由單題改為對話串（點開重現全部輪次並接上續問）、新增「新對話」
按鈕與當前對話高亮、刪除改為刪整串（含 DELETE→POST 回退）。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: 整合驗證 + 收尾

**Files:**
- 無新增；全測 + E2E + schema 部署註記

**Interfaces:**
- Consumes: Task 1–9 全部成果。

- [ ] **Step 1: 後端全測 + lint/format**

Run:
```bash
uv run pytest tests/ -q
uv run black app web tests --check
uv run ruff check app web tests
```
Expected: pytest 全綠；black 無待格式化；ruff 無錯（既有告警不增）。

- [ ] **Step 2: 前端語法**

Run: `node --check web/static/app/ask.js && echo ok`
Expected: `ok`。

- [ ] **Step 3: Playwright 端到端（真實服務）**

確保已 `make schema`（conversation_id 欄就位）並重啟服務載入新碼。用 playwright-skill 跑完整劇本：
1. 登入 → 問答模式 → 問 A → 追問 A2（驗證 Network 帶相同 conversation_id、脈絡延續）。
2. 點舊輪 `[n]` → 開正確報告 modal。
3. 新對話 → 問 B → 側欄兩串。
4. 切回 A 串 → 重現全部輪次 → 續問 A3 → append 同串。
5. 讚/倒讚某輪 → 重整後（重開該串）回饋仍在。
6. 刪 B 串 → 消失。
7. 舊 NULL 列相容：直接打 `GET /api/conversations` 應含舊單題列為單題對話、可點開。

Expected: 七項皆通過；無 console error（除既有已知者）。

- [ ] **Step 4: 更新 spec 狀態並收尾提交**

把 `docs/superpowers/specs/2026-06-22-conversational-qa-multiturn-design.md` 的「狀態」改為「已實作」。

```bash
git add docs/superpowers/specs/2026-06-22-conversational-qa-multiturn-design.md
git commit -m "$(cat <<'EOF'
docs(ask): 多輪對話 spec 標記為已實作

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: 推送並開 PR**

```bash
git push -u origin feat/conversational-qa
gh pr create --base main --title "feat(ask): 問答多輪對話（conversational RAG）" \
  --body "$(cat <<'EOF'
## 摘要
- 問答由單輪無狀態升級為多輪對話式 RAG：同串可連續追問
- 追問先以 Haiku 改寫成獨立查詢＋意圖合一，再檢索；先前對話內嵌 prompt
- qa_log 加 conversation_id；側欄改對話串、可重開續問、刪整串
- 每輪來源獨立掛載，[n] 不再點到別輪報告

## 部署
- **拉碼前先 `make schema`**（新增 conversation_id 欄）後重啟服務

## 測試
- 後端：pytest 全綠（intent/answer 新增測試）
- 前端：Playwright E2E（多輪、切串續問、[n] 對應、新對話、刪整串、舊列相容）

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

**1. Spec coverage（spec 各節 → 任務）:**
- §4 資料模型（conversation_id 欄/索引/COALESCE 分組/舊列相容）→ Task 1、4。
- §5.1 請求契約（conversation_id 進出）→ Task 5、6、8。
- §5.2 answer_question 流程（載歷史/分支/檢索/log/done）→ Task 5。
- §5.3 改寫＋意圖合一 → Task 2。
- §5.4 prompt 組裝（先前對話區塊）→ Task 3。
- §5.5 端點（conversations 清單/取/刪+POST）→ Task 6。
- §6.1 對話串版面 → Task 7。
- §6.2 每輪來源獨立掛載 → Task 7。
- §6.3 側欄對話串/新對話/刪整串 → Task 9。
- §6.4 前端 conversation 狀態 → Task 7（回填）+ Task 8（送出）。
- §7 邊界（舊 NULL 列/離題/prompt 大小/韌性/防注入）→ Task 4（COALESCE、排除離題）、Task 3（截斷）、Task 5（沿用串流/韌性）。
- §8 測試策略 → 各任務 TDD + Task 10 E2E。
全節皆有對應任務，無缺口。

**2. Placeholder scan:** 無 TBD/TODO；每個 code step 皆含完整程式碼；驗證步驟含實際指令與預期。前端因無 JS 測試框架，改以 `node --check` + Playwright 明確劇本（已在步驟列出斷言，非「測試上述」式佔位）。

**3. Type consistency:**
- `condense_and_classify(history_text, question) -> (str, bool)`：Task 2 定義、Task 5 使用一致。
- `load_recent_turns -> list[tuple[str,str]]`（舊→新）：Task 4 定義、Task 3 `build_history_block` 消費、Task 5 呼叫一致。
- `build_user_prompt(question, context, history_block="")`：Task 3 定義、Task 5 三參數呼叫一致。
- `done` payload 含 `conversation_id`：Task 5 產生、Task 7 前端讀 `evt.data.conversation_id` 一致。
- `TurnObj` 欄位（`sources/extSources/qaId/answerEl/actionsEl/srcEl/extEl`）：Task 7 定義、Task 9 `loadConversation` 沿用一致。
- 端點路徑 `/api/conversations[/{id}][/delete]`：Task 6 定義、Task 9 前端呼叫一致。
無不一致。
