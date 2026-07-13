# M3 問答 UX 補齊 — 設計規格

**里程碑**：研報／問答改善 backlog M3（承 M0 共用地基 #58、M1 eval #59、M2 rerank #61）。
**藍圖來源**：`docs/QA_REDESIGN.md`（Claude-like 問答互動）+ `docs/IMPLEMENTATION_PLAN.md` M3。
**日期**：2026-07-13
**分支（規劃）**：`feat/m3-qa-ux`（疊於 M2 tip；M0/M1/M2 併 main 後 rebase）。

## 目標

讓問答頁的互動補齊到「Claude/ChatGPT 級」的五個能力，其中「重新生成」與「編輯重送」走**後端正解**（qa_log 資料模型擴充，而非純前端障眼法）：

1. **重新生成**（regenerate）— 對同一問題重跑，保留舊答案，前端以 `‹ 1/2 ›` 版本切換器回看。
2. **停止生成**（stop）— 使用者中斷串流，保留已串出的部分答案，可據以重新生成；**不誤標為錯誤**。
3. **編輯重送**（edit & resubmit）— 編輯過去的提問並重送，截斷該輪之後的所有輪次。
4. **追問建議**（follow-up suggestions）— 答完後給 3 條可點的財經追問，由 Haiku 非同步產生。
5. **可展開思考步驟**（thinking steps）— 展開/收合已存在；本里程碑補齊「持久化」使歷史重載仍可重現。

## 非目標（YAGNI）

- 不改檢索/排序/融合路徑（M1 eval 不受影響）。
- 不動總覽（overview）與離題（off-topic）的答題邏輯；追問建議僅覆蓋主 RAG 成功路徑。
- 不做跨對話分支樹（editing 只做線性截斷，不保留被截斷分支的可切換 UI）。
- 不改 `select_reports`、`report.py` 生成邏輯（除非追問/版本欄位順帶影響 `report_doc.qa_id` 對應，見「相容性」）。

## 全域約束（Global Constraints，逐字沿用專案慣例）

- **共用工作樹**：只 `git add <明確路徑>`，禁止 `git add -A`/`.`；提交前 `git diff --staged --stat` 驗範圍。
- **繁體中文**回覆使用者；程式碼/識別字/路徑保留原文。**不加裝飾 emoji**。
- **Commit**：Conventional Commits + 繁中 scope（如 `feat(問答): ...`）；訊息結尾 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- **Schema 變更走 `db/schema.sql` 冪等補欄**（`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`），無遷移工具；套用經 `make schema`。
- **`_log_qa` 失敗永不影響已回給使用者的答案**（best-effort，既有 try/except 語意保留）。
- **前端測試**：`vitest run`（非 `node --test`）；每個新元件/reducer 分支附測。
- **後端測試**：`uv run pytest -q` 全綠、零既有測試回歸。
- **fail-open**：追問生成、版本/截斷 DB 寫入任一失敗，都不得中斷主答題或壞掉頁面。

## 資料模型：`research.qa_log` 冪等補欄

沿用 `COALESCE(欄, id)` 慣例（既有 `conversation_id` 即此模式），新增五欄，全部對舊列語意安全：

```sql
-- 版本群組鍵：同一問題的多次重新生成共用；NULL（原始/舊列）以自身 id 為群組（冪等補欄）
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS root_qa_id uuid;
-- 有效列旗標：重新生成的舊版本、編輯截斷的下游輪次設為 false，歷史/續問查詢僅取 true（冪等補欄）
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;
-- 思考步驟（stage 名稱序列），供歷史重現思考卡（冪等補欄）
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS stages jsonb;
-- 追問建議（字串陣列），供歷史重現追問 chips（冪等補欄）
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS followups jsonb;
-- 停止標記：使用者中斷串流時保存的部分答案列（冪等補欄）
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS stopped boolean NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS idx_qa_log_root
    ON research.qa_log ((COALESCE(root_qa_id, id)), created_at);
```

**對舊列語意安全**：`active`/`stopped` 皆 `DEFAULT ... NOT NULL`，ALTER 後所有既有列 = 有效、非停止；`root_qa_id/stages/followups` 為 NULL，查詢以 `COALESCE(root_qa_id, id)` 與空值退化處理。`report_doc.qa_id` 外鍵不受影響（被 supersede 的舊列不刪，只是 `active=false`，外鍵仍有效、可回復）。

**版本群組鍵**：一個問題的所有版本（原始 + 每次重新生成）共用 `COALESCE(root_qa_id, id)`。原始列 `root_qa_id=NULL`（群組鍵=自身 id）；重新生成的新列 `root_qa_id=原始列 id`。此鍵獨立於 `conversation_id`（後者分組一整串對話）。

## 後端設計

### `_log_qa` 擴充（`app/services/answer.py`）

新增 keyword-only 參數，全部有預設值故既有呼叫點零改動語意：

```
async def _log_qa(..., *, conversation_id=None, thinking_ms=None,
                  root_qa_id: str | None = None,
                  stages: list[str] | None = None,
                  followups: list[str] | None = None) -> str
```

INSERT 語句加入 `root_qa_id, active, stages, followups` 四欄（`active` 一律寫 true；停用是後續 UPDATE 的事）。`stages`/`followups` 以 `json.dumps` 寫 jsonb（沿用 sources 寫法）；`root_qa_id` 傳 str 或 None。

### stage 收集（讓 `stages` 有值）

`answer_question` 與 `_answer_overview` 目前散落多個 `yield ("status", {"stage": ...})`。改法：各自維護 `stages_seen: list[str] = []`，以一個小 helper 收斂——

```
def _status(stage: str, **extra):
    stages_seen.append(stage)
    return ("status", {"stage": stage, **extra})
```

把每個 `yield ("status", {"stage": S, ...})` 改為 `yield _status(S, ...)`。各終止分支的 `_log_qa` 帶 `stages=stages_seen`。純機械替換，不改事件輸出（payload 逐位元不變）。

### 重新生成路徑（`regenerate_of`）

`answer_question` 新增 keyword-only 參數 `regenerate_of: str | None = None`：

1. 若有值：先讀舊列 `(root_qa_id, conversation_id)`（fail-open：讀不到就當普通新問題）。以舊列的 `conversation_id` 覆蓋（確保續在同串）。
2. 開始作答前，把舊列 `active=false`（新 helper `_deactivate_qa(old_id)`，UPDATE 單列，best-effort）。
3. 正常跑主 RAG；`_log_qa` 帶 `root_qa_id = 舊列.root_qa_id or 舊列.id`。
4. 各 `done` 事件加 `root_qa_id` 與 `version_count`（該群組 active/inactive 全部計數）欄位，供前端 pager 更新。

**續問歷史一致**：`regenerate_of` 場景下 `load_recent_turns` 因 `AND active` 排除剛停用的舊版本，重跑用的多輪脈絡不含被取代答案。

### 編輯重送路徑（`edit_of`）

`answer_question` 新增 keyword-only 參數 `edit_of: str | None = None`（與 `regenerate_of` 互斥）：

1. 讀被編輯列 `(conversation_id, created_at)`。以其 `conversation_id` 覆蓋。
2. **截斷**：`_truncate_from(conversation_id, created_at)` — `UPDATE research.qa_log SET active=false WHERE COALESCE(conversation_id, id)=:cid AND created_at >= :ts`（停用該輪與其後全部；best-effort）。
3. 用**編輯後的新問題**正常作答，`_log_qa` 為全新輪次（`root_qa_id=NULL`，新群組），同 `conversation_id`。
4. `done` 照常（新 qa_id）。被截斷輪次的 `report_doc`/`feedback` 掛在 inactive 列上，不孤兒、可回復。

### 停止落庫（`POST /api/ask/stop`）

停止採「前端回報部分答案 → 後端寫一列 `stopped=true`」而非伺服器端偵測斷線（見「決策：停止」）。

新增函式 `log_stopped_qa(...)`（`app/services/answer.py`）與端點 `POST /api/ask/stop`：

```
POST /api/ask/stop
body: { question, conversation_id?, partial_answer, sources?, ext_sources?, stages?,
        regenerate_of? }     # regenerate_of：若停止的是重新生成中的一版，帶原 qa_id 續版本鏈
→ { qa_id }
```

`log_stopped_qa` = 一次 INSERT（沿用 `_log_qa` 骨架），寫 `answer=partial_answer, stopped=true, active=true, stages=..., sources=..., conversation_id=...`，`root_qa_id` 依 `regenerate_of` 解析（無則 NULL、自成群組），回傳新 qa_id。best-effort：DB 失敗仍回傳前端可掛的 qa_id（沿用 `_log_qa` 語意）。回傳的 qa_id 讓停止版可按讚、可作為 `regenerate_of` 續版本鏈。

**信任邊界**：partial_answer/sources 由前端回報——這是使用者自己的對話紀錄、且文字本就是本伺服器數秒前串給前端的，best-effort 記錄，可接受（與既有 `_log_qa` best-effort 一致）。

### 追問建議（Haiku 非同步，主 RAG 成功路徑）

在主路徑 `done` 事件**之後**、生成器 return **之前**追加：

1. 呼叫 `generate_followups(question, body, model=HAIKU)`（新函式，`app/services/followups.py`）— 以 Haiku 產 3 條「同語料範疇、財經、具體」的追問；離題/無語料不會走到此路徑。回 `list[str]`（≤3）；任何失敗/逾時回 `[]`（fail-open）。
2. `[]` 就不 yield（前端無 chips）；非空則：best-effort `UPDATE qa_log SET followups=... WHERE id=:qa_id`，再 `yield ("followups", followups)`。
3. 因在 `done` 後才產，主答案顯示零延遲；chips 稍後補到。

`generate_followups` 產生的 prompt 要求：貼合本輪問題與答案、限財經研報可回答範疇、輸出純 JSON 陣列（沿用 `_loads_robust` 之類的健壯解析，避免 `[n]` 汙染）。

### 新增/修改的查詢（全部加 `AND active`）

| 函式 | 變更 |
|---|---|
| `load_recent_turns` | `WHERE ... AND active AND stopped IS NOT TRUE`（多輪脈絡只取有效、完整版本；停止的部分答案不進脈絡，避免半截答案誤導續問） |
| `get_conversation` | `WHERE ... AND active`（含停止列，供歷史重現）；SELECT 加 `stages, followups, root_qa_id, stopped`；每列附 `version_count`（同群組計數，含 inactive）；`history_item` 對應擴充 |
| `list_conversations` | `turn_count`/`title` 的 FILTER 加 `AND active`（截斷/舊版本不灌水；停止列計入，是使用者可見的真實輪次） |
| `/api/history` | `WHERE ... AND active`（可選；側欄歷史一致） |

### 版本回看端點（歷史重載用）

現場（同一 session）重新生成時，前端手上已有舊版本內容，pager 純前端切換、免抓後端。**歷史重載**時 `get_conversation` 只回有效（最新）版本 + `version_count`；若 `version_count>1`，前端顯示 pager，點 `‹` 時才 lazy 抓：

```
GET /api/qa/{root_qa_id}/versions
→ [{qa_id, answer, sources, ext_sources, thinking_ms, stages, feedback, created_at}, ...]  # 該群組全部版本，由舊到新
```

後端 `list_qa_versions(root_qa_id)`：`WHERE COALESCE(root_qa_id, id)=:root ORDER BY created_at ASC`（含 inactive）。

## 前端設計

### `askReducer.ts` — Turn 模型與動作

`Turn` 擴充：

```
phase: 'thinking' | 'streaming' | 'done' | 'notice' | 'error' | 'stopped'   // 新增 stopped
followups: string[]          // 追問 chips
stages: AskStage[]           // （已存在）歷史重載時由 turnFromHistory 填入
priorVersions: TurnVersion[] // 重新生成保留的舊版本（由舊到新）
versionIndex: number         // 目前顯示第幾版（= priorVersions.length 表示最新/現用版）
rootQaId: string | null      // 版本群組鍵
versionCount: number         // 後端回報的總版本數（歷史重載用；現場 = priorVersions.length+1）
```

`TurnVersion = { answer, sources, extSources, qaId, thinkingMs, stages, feedback, followups }`（回看用的唯讀快照）。

顯示規則：`versionIndex === priorVersions.length` → 顯示現用（live）欄位；否則顯示 `priorVersions[versionIndex]` 快照（唯讀，不顯示 like/dislike 寫入）。

新增/調整動作：
- `ask-stop`（新）：`phase → 'stopped'`，保留 `answer`，寫入 `POST /api/ask/stop` 回傳的 `qaId`（使停止版可按讚、可續版本鏈）；不觸發 error 文案。
- `ask-end`（改）：終止態集合加入 `'stopped'`（`if phase in {notice,done,error,stopped} return t`），使停止後尾隨的 for-await 結束不會把 `'stopped'` 覆寫成 `'error'`。
- `regenerate-start`（新）：把現用版本快照 push 進 `priorVersions`，重設 live 串流欄位（`answer=''`、`phase='thinking'`、`stages=['understanding']`、`sources=[]` …），`versionIndex = priorVersions.length`。
- `done`（改）：讀 `root_qa_id`/`version_count` 寫入 `rootQaId`/`versionCount`。
- `followups`（新）：`turn.followups = ev.data`。
- `set-version`（新）：切換 `versionIndex`（pager `‹ ›`）。
- `load`（`turnFromHistory` 改）：填 `stages`、`followups`、`rootQaId`、`versionCount`（來自 `get_conversation`）；`item.stopped` → `phase='stopped'`（重現停止輪次）。

### `askSchemas.ts`

- `askStage` 不變；`askDoneData` 加 `root_qa_id?`, `version_count?`。
- 新事件 `followups`：`AskEvent` 聯集加 `{ event: 'followups'; data: string[] }`；`parseAskEvent` 加 `case 'followups'`（`z.array(z.string())`）。
- `conversationTurnSchema` 加 `stages`（`z.array(askStage).catch([])`）、`followups`（`z.array(z.string()).catch([])`）、`root_qa_id`（nullable）、`version_count`（`z.number().default(1)`）、`stopped`（`z.boolean().default(false)`）。
- 新增 `qaVersionSchema` + `getQaVersions(rootId)`、`stopAsk(body)→{qa_id}`（`askApi.ts`）。

### `useAskController.ts`

- `streamAsk` body 擴充：可帶 `regenerate_of?` / `edit_of?`。
- 新方法：
  - `stop()`（async）：先 `++reqId`（丟棄殘留 SSE、latest-wins）、`askCtrl.current?.abort()`；再以目前串流 turn 的手上內容（`answer`/`sources`/`stages`）`await stopAsk({question, conversation_id, partial_answer, sources, stages, regenerate_of?})` 取回 `qa_id`；最後 `dispatch({type:'ask-stop', id: turnId, qaId})`。需在 controller 記住「目前串流中的 turnId」。stopAsk 失敗（網路/DB）→ 仍 dispatch ask-stop（qaId 用本地暫存或 null，fail-open 不擋停止）。
  - `regenerate(turnId, qaId, question)`：`dispatch regenerate-start`，發 `streamAsk({question, conversation_id, regenerate_of: qaId})`，事件回灌同 `turnId`（不 append 新 turn）。done 後更新 pager。
  - `editResubmit(turnId, qaId, newQuestion)`：本地把該 turn 之後的 turns 移除（reducer `truncate-after`），改該 turn 的 question，發 `streamAsk({question:newQuestion, conversation_id, edit_of: qaId})` 回灌同 turnId。
  - `setVersion(turnId, index)` / `loadVersions(turnId, rootId)`（歷史 pager lazy 抓）。
- `submit`/`regenerate`/`editResubmit` 共用一個內部 streaming 函式（DRY），差別只在 body 與「append 新 turn vs 回灌既有 turn」。

### 元件

- **`Composer.tsx`**：`disabled=busy` 時，送出鈕改為**停止鈕**（`onStop`）。`AskPage` 傳 `onStop={ctrl.stop}`。
- **`UserMessage.tsx`**：由純顯示改為可編輯——hover 顯示編輯鈕 → 就地 `textarea` + 送出/取消。需要 `turn`/`onEdit(newText)`；`AskPage` 接 `ctrl.editResubmit`。新增 `UserMessage.test.tsx`（目前唯一無測元件）。
- **`AssistantMessage.tsx`**：
  - `showActions` 由 `phase==='done'` 擴為 `(phase==='done' || phase==='stopped') && !isOfftopic`；停止版有 qaId，故按讚/複製/來源皆可用，並顯示「已停止」標記。
  - actions 加**重新生成鈕**（`onRegenerate`）；`error`/`stopped` 分支也提供重新生成（停止版帶其 qaId 作 `regenerate_of`，續版本鏈）。
  - 版本 pager `‹ index+1/versionCount ›`（`versionCount>1` 才顯示）；點擊 `onSetVersion`。
  - 顯示內容改讀「目前版本」（live 或 `priorVersions[versionIndex]`）。
  - 追問 chips：`followups.length>0 && phase 終止` 時，於 actions 下方渲染 chips，點擊 `onFollowup(text)` → `submit(text)`。
- **`ThinkingSteps.tsx`**：不改邏輯（展開已存在）；確認歷史重載時 `turn.stages` 已由 `turnFromHistory` 填入即可重現。
- **`AskPage.tsx`**：`busy` 時 Composer 顯示停止；wire `onRegenerate`/`onEdit`/`onSetVersion`/`onFollowup`。

## 決策紀要

- **停止（stop）落庫**（使用者定案）：停止寫一列 `stopped=true`，供歷史重現、按讚、以版本鏈重新生成。採「前端回報部分答案 → `POST /api/ask/stop`」而非「伺服器端偵測斷線後補寫」——後者需在 async 生成器被取消（`GeneratorExit`/`CancelledError`）時 `asyncio.shield` 住 `_log_qa` 的 await，取消語意脆弱且難以穩定測試；前端回報可靠、可測、回傳真 qa_id。信任邊界可接受（best-effort、使用者自己的紀錄、文字源自本伺服器）。停止的部分答案**不進多輪脈絡**（`load_recent_turns` 加 `AND stopped IS NOT TRUE`），避免半截答案誤導續問；但**計入**側欄輪次與歷史顯示。
- **版本 pager 現場 vs 重載**：現場切換純前端（手上有舊版本）；重載時最新版即時可見、`version_count>1` 顯示 pager、點 `‹` 才 lazy 抓舊版（避免 `get_conversation` 熱路徑做遞迴鏈查詢）。
- **追問只覆蓋主 RAG 成功路徑**：overview/off-topic/no-context 不給 chips（no-context 無可追問、off-topic 不鼓勵、overview 是聚合題另路徑）。留待日後里程碑視需要擴充。
- **`root_qa_id` 群組鍵**（非 supersedes 連結串）：對齊既有 `COALESCE(conversation_id, id)` 慣例，pager 全版本重建為單一 `WHERE COALESCE(root_qa_id, id)=:root` 查詢，無需遞迴 CTE。

## 相容性與風險

- **`report_doc.qa_id`**：重新生成不刪舊列（只 `active=false`），既有研報外鍵仍有效。編輯截斷同理（掛 inactive 列、可回復）。
- **`history_item` 位置式 tuple**：`get_conversation`/`/api/history` 的 SELECT 擴欄會動到 `history_item(row)` 的位置式解構——這是 M0 想消滅但歷史列仍是裸 tuple 的既知風險；擴欄時逐一對位、加測鎖定。
- **多輪脈絡**：所有分組查詢加 `AND active` 後，被取代/截斷列一律不進脈絡與側欄計數——需回歸測確認既有多輪測試不破。
- **SSE `followups` 在 `done` 之後**：前端 `done` handler 已設 conversationId 並繼續讀串流；`followups` 事件在其後 dispatch 正常。需測「done 後仍能收 followups」。

## 測試策略（TDD）

**後端**（pytest）：
- `_log_qa` 寫入 `root_qa_id/stages/followups`、`active=true`。
- `_deactivate_qa`/`_truncate_from` 正確停用、rowcount、fail-open。
- `regenerate_of`：舊列停用、新列 `root_qa_id` 綁定、`done` 帶 `version_count`、`load_recent_turns` 排除舊版。
- `edit_of`：截斷該輪及其後、新輪新群組、`report_doc` 不孤兒。
- `log_stopped_qa`：寫 `stopped=true, active=true`、`root_qa_id` 依 `regenerate_of` 解析、回傳 qa_id、DB 失敗 fail-open；`load_recent_turns` 排除停止列、`get_conversation` 含停止列並帶 `stopped` 旗標。
- `generate_followups`：正常 3 條、JSON 健壯解析、失敗回 `[]`、離題/no-context 不呼叫。
- `get_conversation`/`list_conversations`：`AND active` 過濾、`stages/followups/version_count` 正確。
- `list_qa_versions`：群組全版本、由舊到新。
- 既有 `test_answer.py`/多輪/歷史測試零回歸。

**前端**（vitest）：
- reducer：`ask-stop`→stopped+寫 qaId、`ask-end` 不覆寫 stopped、`regenerate-start` 快照+重設、`followups`、`set-version`、`truncate-after`、`turnFromHistory` 填新欄（含 `stopped`→phase）。
- `AssistantMessage`：stopped 顯示部分答案+「已停止」標記+重生鈕、pager 顯示/切換、追問 chips 點擊、showActions 擴充。
- `UserMessage`：編輯開關+送出/取消（新測檔）。
- `Composer`：busy 顯示停止鈕、`onStop` 觸發。
- `useAskController`：stop（含 `stopAsk` 回報 + fail-open）/regenerate/editResubmit 的 latest-wins 與 body 正確（mock streamAsk/stopAsk）。

## 部署

無破壞性 schema 變更（僅冪等補欄）；上線 = 併 main → `make schema`（套新欄）→ 重建前端 `dist` → `sudo systemctl restart report-mark-web.service`。`generate_followups` 走既有 `claude` CLI（Haiku），無新依賴。
