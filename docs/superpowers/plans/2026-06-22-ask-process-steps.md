# 問答「處理過程」步驟面板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在問答回答前後，於每輪答案上方顯示系統實際處理步驟（理解問題→找到 N 篇→閱讀整理→搜尋網路→生成回答）的可收折面板。

**Architecture:** 後端 `answer_question` 在真實流程邊界 yield 新的 `status` 事件（`understanding`/`retrieved`/`reading`，並把既有 `searching_web` 改成物件），前端 `ask.js` 用一個步驟清單面板消費這些事件並逐步點亮，答案串流後面板保留可收折。歷史回看以既有 `sources`/`ext_sources` 重建靜態面板，零 DB 改動。

**Tech Stack:** Python 3.13 + 既有 async 產生器（`app/services/answer.py`）、unittest（`tests/test_answer.py`）、原生 ESM 前端（`web/static/app/ask.js`）、index.html 內嵌 CSS。

## Global Constraints

- 顯示「系統處理步驟」，**不**顯示模型 extended thinking / 推理 token。
- 圖示一律 inline SVG，**不用 emoji**（遵循專案既有 SVG icon 慣例與無-emoji 偏好）。
- 不改 DB schema；歷史回看以既有 `qa_log.sources` / `qa_log.ext_sources` 重建。
- 回答全用繁體中文。
- `status` 事件 payload 統一為物件 `{"stage": "...", ...}`。
- `web/server.py::_sse` 是泛型透傳，**不得改動**。
- 後端改動部署後需重啟 `report-mark-web.service`；前端為 `/static` no-cache 靜態檔，部署即時生效。

---

### Task 1: 後端發送處理步驟 status 事件

**Files:**
- Modify: `app/services/answer.py`（`answer_question`，約 489–549 行）
- Test: `tests/test_answer.py`（新增 1 個測試 + 更新 3 處既有斷言）

**Interfaces:**
- Produces（前端與測試依賴的事件序，正常路徑）：
  `("status", {"stage": "understanding"})` →
  `("sources", [...])` →
  `("status", {"stage": "retrieved", "count": <int>})` →
  `("status", {"stage": "reading"})` →
  `("token", <str>)*` →
  `("status", {"stage": "searching_web"})`（條件性，僅 WebSearch 觸發）→
  `("ext_sources", [...])` →
  `("done", {...})`
- 離題路徑：`("status", {"stage": "understanding"})` → `("sources", [])` → `("notice", <str>)` → `("done", {...})`
- 無脈絡路徑：`…understanding → sources → retrieved(count) → ("token", NO_CONTEXT_MESSAGE) → done`（**無** reading，因不呼叫 LLM）

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_answer.py` 的 `AnswerGateTests` 類別內（緊接 `test_on_topic_intent_calls_llm` 之後）新增：

```python
    async def test_emits_process_status_steps(self):
        # 在領域問題：事件序須含 understanding(開頭) → retrieved(count) → reading(token 前)
        from app.services import answer as ans

        called = {"llm": False, "intent": False}
        orig = self._patch(ans, in_domain=True, called=called)
        try:
            events = [e async for e in ans.answer_question("可口可樂的投資評級如何")]
        finally:
            self._restore(ans, orig)

        # 第一個事件即 understanding（在檢索/來源之前）
        self.assertEqual(events[0], ("status", {"stage": "understanding"}))

        statuses = [p for k, p in events if k == "status"]
        # retrieved 帶實際來源數（fake_search 回 1 列）
        self.assertIn({"stage": "retrieved", "count": 1}, statuses)
        self.assertIn({"stage": "reading"}, statuses)

        # 順序：understanding(0) < retrieved < reading < 第一個 token
        i_retrieved = next(i for i, (k, p) in enumerate(events)
                           if k == "status" and p.get("stage") == "retrieved")
        i_reading = next(i for i, (k, p) in enumerate(events)
                         if k == "status" and p.get("stage") == "reading")
        i_token = next(i for i, (k, _) in enumerate(events) if k == "token")
        self.assertLess(0, i_retrieved)
        self.assertLess(i_retrieved, i_reading)
        self.assertLess(i_reading, i_token)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run python -m pytest tests/test_answer.py::AnswerGateTests::test_emits_process_status_steps -v`
Expected: FAIL（目前第一個事件是 `sources`，無 `understanding`/`retrieved`/`reading` status）

- [ ] **Step 3: 在 answer_question 插入 status 事件**

於 `app/services/answer.py`：

(a) 在 `conv_id = conversation_id or str(uuid.uuid4())` 之後、`turns = ...` 之前插入一行：

```python
    conv_id = conversation_id or str(uuid.uuid4())
    yield ("status", {"stage": "understanding"})  # 步驟1：理解問題（含意圖判定/改寫）
```

(b) 在 `yield ("sources", [asdict(s) for s in sources])` 之後插入一行：

```python
    sources, context = build_context(scored)
    yield ("sources", [asdict(s) for s in sources])
    yield ("status", {"stage": "retrieved", "count": len(sources)})  # 步驟2：找到 N 篇
```

(c) 在送 `stream_completion` 之前（`searching_sent = False` 之後、`async for chunk in stream_completion(` 之前）插入一行：

```python
    searching_sent = False
    yield ("status", {"stage": "reading"})  # 步驟3：閱讀重點、整理回答
    async for chunk in stream_completion(
```

(d) 把既有的 `yield ("status", "searching_web")` 改為物件：

```python
        if chunk == SEARCH_EVENT:
            if not searching_sent:
                searching_sent = True
                yield ("status", {"stage": "searching_web"})  # 步驟4：搜尋網路補充
            continue
```

- [ ] **Step 4: 跑新測試確認通過**

Run: `uv run python -m pytest tests/test_answer.py::AnswerGateTests::test_emits_process_status_steps -v`
Expected: PASS

- [ ] **Step 5: 跑整檔，修正被新事件打破的 3 處既有斷言**

Run: `uv run python -m pytest tests/test_answer.py -v`
Expected: 3 個既有測試失敗（事件序起點變成 `status`）。逐一修正：

修正 1 — `test_off_topic_intent_skips_llm`（原約 302 行）：

```python
        kinds = [k for k, _ in events]
        self.assertEqual(kinds, ["status", "sources", "notice", "done"])  # 開頭多 understanding
        self.assertEqual(events[0], ("status", {"stage": "understanding"}))
        self.assertEqual(events[1][1], [])  # 離題不顯示任何來源
        self.assertEqual(events[2][1], ans.OFF_TOPIC_MESSAGE)
        self.assertEqual(events[3][0], "done")
        self.assertEqual(events[3][1]["cited"], [])
        self.assertIn("conversation_id", events[3][1])
```

（同時把該測試後續 `events[1]/events[2]` 等索引一併下移一位，如上所示；`called["intent"]` 與 `called["llm"]` 斷言不變。）

修正 2 — `test_on_topic_intent_calls_llm`（原約 322–323 行）：

```python
        kinds = [k for k, _ in events]
        self.assertEqual(kinds[0], "status")        # 開頭為 understanding status
        self.assertIn("sources", kinds)
        srcs = next(p for k, p in events if k == "sources")
        self.assertTrue(len(srcs) >= 1)             # 有來源
```

（移除原本 `self.assertEqual(kinds[0], "sources")` 與 `self.assertTrue(len(events[0][1]) >= 1)`，改為上面以 kind 取 sources 的寫法；`("token", "答案[1]")`、`events[-1]` 等其餘斷言不變。）

修正 3 — `test_body_excludes_sentinel_and_emits_ext_sources`（原約 381 行）：

```python
        self.assertEqual([k for k, _ in events][0], "status")   # 事件序起點＝understanding
```

- [ ] **Step 6: 跑整檔確認全綠 + lint**

Run: `uv run python -m pytest tests/test_answer.py -v`
Expected: PASS（全部）

Run: `uv run black app/services/answer.py tests/test_answer.py && uv run ruff check app/services/answer.py tests/test_answer.py`
Expected: 無錯誤

- [ ] **Step 7: Commit**

```bash
git add app/services/answer.py tests/test_answer.py
git commit -m "$(cat <<'EOF'
feat(ask): answer_question 發送處理步驟 status 事件

於真實流程邊界 yield understanding/retrieved(count)/reading，並把
searching_web 改為物件格式，供前端渲染「處理過程」步驟面板。
同步更新受事件序起點變動影響的 3 處既有測試斷言。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 前端即時「處理過程」步驟面板

**Files:**
- Modify: `web/static/app/ask.js`（`createTurn`、`askQuestion` 事件迴圈；新增 process 面板函式；移除 `thinking`/`searchingWeb`）
- Modify: `web/static/index.html`（CSS：新增 `.ask-process*`，移除已死的 `.ask-thinking`/`.ask-searching-inline`）

**Interfaces:**
- Consumes（Task 1 產生的事件）：`status {stage:"understanding"|"retrieved"(+count)|"reading"|"searching_web"}`、`token`、`notice`、`done`
- Produces（Task 3 會重用的前端函式）：
  - `renderProcess(turn, { expanded = true })` — 在 `turn.processEl` 建面板，所有步驟 pending，`web` 步驟初始隱藏
  - `setStep(turn, key, state, label?)` — `key ∈ {understand, retrieved, reading, web, generate}`，`state ∈ {pending, active, done}`，`label` 可改寫步驟文字
  - `turn.processEl` — 由 `createTurn` 建立的 `.ask-process-host` 節點（在 `.ask-msg-user` 與 `.ask-msg-bot` 之間）

- [ ] **Step 1: createTurn 加入面板宿主節點**

於 `web/static/app/ask.js` `createTurn`，把 `node.innerHTML` 模板與 turn 物件改成（在 user 與 bot 之間插入 `.ask-process-host`、新增 `turn.processEl`）：

```javascript
  node.innerHTML = html`
    <div class="ask-msg-user"></div>
    <div class="ask-process-host"></div>
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
    processEl: node.querySelector(".ask-process-host"),
    actionsEl: node.querySelector(".ask-actions"),
    srcEl: node.querySelectorAll(".ask-sources")[0],
    extEl: node.querySelector(".ask-ext-list"),
  };
```

- [ ] **Step 2: 新增 process 面板函式（緊接 `const SVG = {...};` 區塊之後）**

於 `web/static/app/ask.js`，在 `SVG` 常數定義結束的 `};` 之後插入：

```javascript
// ───── 處理過程步驟面板（顯示系統實際在做什麼；非模型推理）─────
const PROC_STEPS = [
  { key: "understand", label: "理解問題" },
  { key: "retrieved",  label: "檢索研報" },          // 完成時改寫成「找到 N 篇相關研報」
  { key: "reading",    label: "閱讀重點、整理回答" },
  { key: "web",        label: "搜尋網路補充" },        // 條件性：觸發網路搜尋才顯示
  { key: "generate",   label: "生成回答" },
];
const PROC_ICO = {
  pending: `<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>`,
  active: `<span class="ask-step-spin" aria-hidden="true"></span>`,
  done: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3.5 8.5 6.5 11.5 12.5 5"/></svg>`,
};

// 建面板：expanded 控制預設展開（即時輪 true、歷史重建 false）。web 步驟初始隱藏。
function renderProcess(turn, { expanded = true } = {}) {
  const items = PROC_STEPS.map(s => html`<li class="ask-step" data-step="${s.key}" data-state="pending">
      <span class="ask-step-ico">${raw(PROC_ICO.pending)}</span>
      <span class="ask-step-label">${s.label}</span>
    </li>`).join("");
  turn.processEl.innerHTML = html`<div class="ask-process${expanded ? " open" : ""}">
      <button class="ask-process-head" type="button" aria-expanded="${expanded ? "true" : "false"}">
        ${raw(SVG.chev)}<span>處理過程</span>
      </button>
      <ol class="ask-process-steps">${raw(items)}</ol>
    </div>`;
  turn.processEl.querySelector('.ask-step[data-step="web"]').hidden = true;
  const head = turn.processEl.querySelector(".ask-process-head");
  head.onclick = () => {
    const box = turn.processEl.querySelector(".ask-process");
    const open = box.classList.toggle("open");
    head.setAttribute("aria-expanded", open ? "true" : "false");
  };
}

// 設定步驟狀態；label 非 null 時改寫文字。state: pending|active|done
function setStep(turn, key, state, label) {
  const li = turn.processEl.querySelector(`.ask-step[data-step="${key}"]`);
  if (!li) return;
  li.hidden = false;
  li.dataset.state = state;
  li.querySelector(".ask-step-ico").innerHTML = PROC_ICO[state];
  if (label != null) li.querySelector(".ask-step-label").textContent = label;
}
function clearProcess(turn) { turn.processEl.innerHTML = ""; }

// status 事件 → 推進步驟（payload 為 {stage,...} 物件）
function onStatus(turn, data) {
  const stage = data && typeof data === "object" ? data.stage : data;
  if (stage === "understanding") {
    setStep(turn, "understand", "active");
  } else if (stage === "retrieved") {
    setStep(turn, "understand", "done");
    setStep(turn, "retrieved", "done", `找到 ${Number(data.count) || 0} 篇相關研報`);
    setStep(turn, "reading", "active");
  } else if (stage === "reading") {
    setStep(turn, "reading", "active");
  } else if (stage === "searching_web") {
    setStep(turn, "reading", "done");
    setStep(turn, "web", "active");
  }
}

// 第一個 token 抵達：前面步驟收尾、點亮「生成回答」
function startGenerating(turn) {
  setStep(turn, "understand", "done");
  setStep(turn, "retrieved", "done");
  setStep(turn, "reading", "done");
  const web = turn.processEl.querySelector('.ask-step[data-step="web"]');
  if (web && !web.hidden) setStep(turn, "web", "done");
  setStep(turn, "generate", "active");
}

// done：把所有顯示中的步驟標完成
function finishProcess(turn) {
  turn.processEl.querySelectorAll(".ask-step:not([hidden])").forEach(li => {
    if (li.dataset.state !== "done") setStep(turn, li.dataset.step, "done");
  });
}
```

- [ ] **Step 3: askQuestion 啟動面板（取代 thinking）**

於 `askQuestion`，把 `const turn = createTurn(q);` 之後的 `thinking(turn);` 改為：

```javascript
  const turn = createTurn(q);
  renderProcess(turn);
  setStep(turn, "understand", "active");
  toBottom();
```

- [ ] **Step 4: askQuestion 事件迴圈接上步驟推進**

於 `askQuestion` 的事件分派（while 內），把 `status`、`token`、`notice`、`done` 四個分支改為：

```javascript
        if (evt.event === "sources") { turn.sources = evt.data || []; paintSources(turn); }
        else if (evt.event === "status") { onStatus(turn, evt.data); }
        else if (evt.event === "ext_sources") { turn.extSources = (evt.data || []).filter(s => s && safeHttp(s.url)); paintExtSources(turn); }
        else if (evt.event === "token") { if (!started) startGenerating(turn); started = true; turn.answer += evt.data; const stick = nearBottom(); paintAnswer(turn, true); if (stick) toBottom(); }
        else if (evt.event === "notice") { notice = true; started = true; clearProcess(turn); paintNotice(turn, evt.data); toBottom(); }
        else if (evt.event === "done") { turn.qaId = (evt.data && evt.data.qa_id) || null; if (evt.data && evt.data.conversation_id) conversationId = evt.data.conversation_id; finishProcess(turn); }
        else if (evt.event === "error") { clearProcess(turn); fail(turn, "問答服務發生錯誤，請稍後再試。"); return; }
```

- [ ] **Step 5: 收尾分支：無回答時清掉面板**

於 `askQuestion` 收尾區塊，把 `if (!started) fail(...)` 改為先清面板：

```javascript
    if (my === state.askReq) {
      if (!notice) paintAnswer(turn, false);   // 收尾：去掉游標（離題卡不可被覆寫）
      if (!started) { clearProcess(turn); fail(turn, "沒有取得回答，請稍後再試。"); }
      else if (!notice) paintActions(turn);   // 動作列（離題卡不顯示）
      loadAskHistory();   // 刷新側欄對話清單（renderHistory 內已呼叫 markActive）
    }
```

- [ ] **Step 6: 移除已死的 thinking() 與 searchingWeb()**

於 `web/static/app/ask.js` 刪除 `thinking(turn)` 函式（`function thinking(turn) {...}`）與 `searchingWeb(turn)` 函式（含其上方註解兩行）。`fail`、`paintNotice` 等保留不動。

- [ ] **Step 7: 新增 CSS（取代已死的 .ask-thinking/.ask-searching-inline）**

於 `web/static/index.html`，把這兩行：

```css
  .ask-thinking { display: inline-flex; align-items: center; gap: 9px; color: var(--label-3); font-size: 14px; }
  .ask-thinking .spin { width: 16px; height: 16px; border-radius: 50%;
    border: 2px solid var(--sep); border-top-color: var(--brand); animation: spin .8s linear infinite; }
  .ask-searching-inline { display: flex; margin-top: 10px; }   /* 串流中途搜尋的臨時指示 */
```

整段替換為：

```css
  /* 處理過程步驟面板（系統實際處理步驟；非模型推理）*/
  .ask-process-host:empty { display: none; }
  .ask-process { margin: 2px 0 10px; }
  .ask-process-head { display: inline-flex; align-items: center; gap: 5px; height: 28px; padding: 0 6px;
    background: none; border: none; border-radius: 7px; cursor: pointer; color: var(--label-3);
    font-family: inherit; font-size: 13px; transition: background .12s, color .12s; }
  .ask-process-head:hover { background: var(--fill); color: var(--label-2); }
  .ask-process-head .ask-chevron { transition: transform .15s ease; }
  .ask-process.open .ask-process-head .ask-chevron { transform: rotate(90deg); }
  .ask-process-steps { list-style: none; margin: 0; padding: 0;
    max-height: 0; opacity: 0; overflow: hidden; visibility: hidden;
    transition: max-height var(--dur-2) var(--ease-out), opacity var(--dur-2), margin-top var(--dur-2), visibility var(--dur-2); }
  .ask-process.open .ask-process-steps { max-height: 320px; opacity: 1; margin-top: 4px; visibility: visible; }
  .ask-step { display: flex; align-items: center; gap: 9px; padding: 3px 8px; font-size: 13.5px; color: var(--label-3); }
  .ask-step[data-state="active"] { color: var(--label); }
  .ask-step[data-state="done"] { color: var(--label-2); }
  .ask-step-ico { flex: none; width: 16px; height: 16px; display: grid; place-items: center; }
  .ask-step-ico svg { width: 16px; height: 16px; display: block; }
  .ask-step[data-state="done"] .ask-step-ico { color: var(--brand); }
  .ask-step-spin { width: 14px; height: 14px; border-radius: 50%;
    border: 2px solid var(--sep); border-top-color: var(--brand); animation: spin .8s linear infinite; }
```

- [ ] **Step 8: 語法檢查**

Run: `node --check web/static/app/ask.js`
Expected: 無輸出（語法正確）

- [ ] **Step 9: 手動驗證（Playwright）**

啟動服務後（`make serve` 或既有 systemd），用 playwright-skill 登入 `/` → 切問答模式 → 提一個在領域問題（如「台積電最新法說重點」），觀察：
1. 等待時面板逐步點亮：理解問題 → 找到 N 篇相關研報 → 閱讀重點整理回答 →（若有）搜尋網路補充 → 生成回答
2. 答案串流出現後面板保留於答案上方、預設展開
3. 點「處理過程」標題可收折/展開，chevron 旋轉
4. 提一個離題問題 → 面板消失、只顯示「無法回答」提示卡
Expected: 以上皆符合；console 無新錯誤

- [ ] **Step 10: Commit**

```bash
git add web/static/app/ask.js web/static/index.html
git commit -m "$(cat <<'EOF'
feat(ask): 即時「處理過程」步驟面板取代單行 spinner

問答等待時於答案上方逐步點亮處理步驟（理解問題→找到N篇→閱讀整理→
搜尋網路→生成回答），答案串流後保留可收折面板。移除舊 thinking/
searchingWeb 單行指示。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: 歷史回看重建靜態步驟面板

**Files:**
- Modify: `web/static/app/ask.js`（`loadConversation` 的非離題分支；新增 `staticProcess`）

**Interfaces:**
- Consumes：Task 2 的 `renderProcess(turn, {expanded})` 與 `setStep(turn, key, state, label?)`；既有 `turn.sources`、`turn.extSources`
- 設計決策：歷史輪面板**預設收合**（`expanded:false`），避免長對話串雜訊；即時輪維持展開。離題歷史項（`it.is_offtopic`）**不**建面板，與即時離題清掉面板一致。

- [ ] **Step 1: 新增 staticProcess 函式**

於 `web/static/app/ask.js`（與其他 process 函式相鄰，例如 `finishProcess` 之後）新增：

```javascript
// 歷史重建：以既有 sources/ext_sources 還原靜態步驟面板（預設收合）
function staticProcess(turn) {
  renderProcess(turn, { expanded: false });
  setStep(turn, "understand", "done");
  setStep(turn, "retrieved", "done", `找到 ${turn.sources.length} 篇相關研報`);
  setStep(turn, "reading", "done");
  if (turn.extSources.length) setStep(turn, "web", "done");
  setStep(turn, "generate", "done");
}
```

- [ ] **Step 2: loadConversation 非離題分支掛上靜態面板**

於 `loadConversation` 的迴圈，把非離題的 `else` 分支改為（在 `paintAnswer`/`paintActions` 之後、`feedback` 之前插入 `staticProcess(turn)`）：

```javascript
      } else {
        paintAnswer(turn, false); paintActions(turn);
        staticProcess(turn);
        if (it.feedback) {
          const sel = it.feedback === "like" ? "[data-act='like']" : "[data-act='dislike']";
          const btn = turn.actionsEl.querySelector(sel);
          if (btn) btn.classList.add("on");
        }
      }
```

（離題分支 `if (it.is_offtopic) { paintNotice(...) }` 不變，不建面板。）

- [ ] **Step 3: 語法檢查**

Run: `node --check web/static/app/ask.js`
Expected: 無輸出

- [ ] **Step 4: 手動驗證（Playwright）**

提至少一個問題後，從側欄點該對話重新載入（或重整頁面後點側欄歷史項），觀察：
1. 每輪答案上方出現「處理過程」面板、**預設收合**
2. 點開後步驟全為完成態：理解問題 ✓ / 找到 N 篇相關研報 ✓ / 閱讀重點整理回答 ✓ /（若該輪用過網路）搜尋網路補充 ✓ / 生成回答 ✓，N 與該輪實際來源數一致
3. 離題的歷史項只有提示卡、無面板
Expected: 以上皆符合

- [ ] **Step 5: Commit**

```bash
git add web/static/app/ask.js
git commit -m "$(cat <<'EOF'
feat(ask): 歷史回看以既有來源重建靜態處理過程面板

從側欄載入舊對話時，用 qa_log 既有 sources/ext_sources 還原各輪
「處理過程」面板（預設收合、全步驟完成態），零 DB 改動。離題歷史
項不建面板。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**1. Spec coverage：**
- 步驟模型（5 步、searching_web 條件性）→ Task 1 後端事件 + Task 2 前端步驟。✓
- 誠實處理並行（步驟1中性文字）→ Task 1 開頭單一 understanding；Task 2 步驟1文字「理解問題」。✓
- 後端 4 處改動（understanding/retrieved/reading/searching_web 物件化）→ Task 1 Step 3。✓
- `_sse` 不改 → Global Constraints 明列、無任務改動 server.py。✓
- 前端面板取代單行 spinner + 保留可收折 → Task 2。✓
- 邊界：離題清面板（Task 2 Step 4 notice 分支）、無脈絡（Task 1 Interfaces 標明無 reading；Task 2 token 仍會 startGenerating，可接受）、錯誤（Task 2 Step 4 error 分支 clearProcess）。✓
- 歷史重建零 DB 改動 → Task 3。✓
- 圖示 inline SVG 無 emoji → Task 2 PROC_ICO 全 SVG/CSS。✓
- 測試：後端 unittest 擴充 + 修既有斷言 → Task 1；前端無框架以 Playwright 手動 → Task 2/3 手動步驟。✓
- 不做 thinking / 不存時間軸 / 不顯示毫秒 → 無對應任務（正確排除）。✓

**2. Placeholder scan：** 無 TBD/TODO；每個改 code 的 step 都附完整程式碼與確切路徑/指令。✓

**3. Type consistency：**
- 事件 payload `{"stage": ...}` 後端（Task 1）與前端 `onStatus` 取 `data.stage`（Task 2）一致。✓
- `retrieved` 帶 `count`（後端 `len(sources)`）↔ 前端 `Number(data.count)`。✓
- `renderProcess(turn, {expanded})` / `setStep(turn, key, state, label)` 簽名於 Task 2 定義、Task 3 重用，名稱一致。✓
- step keys `understand/retrieved/reading/web/generate` 於 PROC_STEPS、onStatus、startGenerating、staticProcess 全一致。✓
- `turn.processEl`（`.ask-process-host`）於 createTurn 建立、各函式使用，一致。✓
