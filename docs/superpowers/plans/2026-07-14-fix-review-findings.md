# Review Findings Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the review-confirmed build, data-integrity, routing, availability, and frontend-state regressions without changing the public ask API contract.

**Architecture:** Keep a QA version active until its replacement has been persisted, then apply the state transition atomically. Make stream cancellation and stopping idempotent through a per-request client token; move post-answer follow-up work outside the admission semaphore and bound CPU reranking. Frontend state must ignore stale version responses and keep stream termination visible.

**Tech Stack:** Python 3.13, FastAPI, async SQLAlchemy/PostgreSQL, React 19, TypeScript, Vitest, pytest.

## Global Constraints

- Preserve legacy `qa_log` rows with null `root_qa_id` and `conversation_id`.
- Keep SQL parameterized and use transaction boundaries for version-state transitions.
- Do not introduce a new external dependency.
- All request body limits use existing FastAPI/Pydantic validation patterns.
- Run Python tests with `uv run pytest -q -s` while this environment's pytest capture cleanup is broken.

---

### Task 1: Restore frontend compilation and make version loading race-safe

**Files:**
- Modify: `frontend/src/features/ask/AssistantMessage.tsx`
- Modify: `frontend/src/features/ask/AskPage.tsx`
- Modify: `frontend/src/lib/askReducer.ts`
- Modify: `frontend/src/lib/useAskController.ts`
- Modify: `frontend/src/features/ask/AssistantMessage.test.tsx`
- Modify: `frontend/src/lib/askReducer.test.ts`
- Modify: `frontend/src/lib/useAskController.test.ts`

**Interfaces:**
- Consumes: `AnswerView`, `QaVersion[]`, `getQaVersions(rootId)`.
- Produces: `loadVersions()` only commits a non-empty response for the currently requested turn/root pair; cancelled streams end in `stopped` or `error`.

- [ ] **Step 1: Write failing frontend tests**

```ts
test('error answer citation forwards its visible AnswerView', () => {
  render(<AssistantMessage turn={errorTurn} onCite={onCite} {...handlers} />)
  fireEvent.click(screen.getByRole('button', { name: '1' }))
  expect(onCite).toHaveBeenCalledWith(1, expect.objectContaining({ qaId: 'qa1' }))
})

test('empty version response keeps the current version metadata', () => {
  const state = askReducer(withVersionedTurn, { type: 'load-versions', id: 't1', versions: [] })
  expect(state.turns[0].versionCount).toBe(2)
})
```

- [ ] **Step 2: Run the two tests and verify they fail**

Run: `npm test -- --run frontend/src/features/ask/AssistantMessage.test.tsx frontend/src/lib/askReducer.test.ts`

Expected: the citation test reports a TypeScript callback incompatibility or missing second argument; empty versions resets the count.

- [ ] **Step 3: Implement the smallest state fixes**

```ts
// AssistantMessage error branch
renderAnswer(turn.answer, turn.sources.length, n => onCite(n, visibleAnswerView(turn)), turn.sources)

// askReducer load-versions branch
if (action.versions.length === 0) return t
```

Add a monotonically increasing `versionRequestId` ref in `useAskController`; only dispatch loaded versions when the requested turn id and root id still match. Mark a stream displaced by `abortAll` as stopped before replacing it.

- [ ] **Step 4: Run focused frontend tests and TypeScript build**

Run: `npm test -- --run frontend/src/features/ask/AssistantMessage.test.tsx frontend/src/lib/askReducer.test.ts frontend/src/lib/useAskController.test.ts && npm run build`

Expected: all selected tests pass and `tsc --noEmit` completes.

### Task 2: Make QA replacement and stream stopping durable and idempotent

**Files:**
- Modify: `app/services/answer.py`
- Modify: `web/server.py`
- Modify: `frontend/src/lib/askApi.ts`
- Modify: `frontend/src/lib/useAskController.ts`
- Modify: `tests/test_answer.py`
- Modify: `tests/test_ask_stop_endpoint.py`
- Modify: `frontend/src/lib/useAskController.test.ts`

**Interfaces:**
- Consumes: `regenerate_of`, `edit_of`, and a new optional opaque `request_id` from ask/stop.
- Produces: successful replacement persistence and deactivation occur in one database transaction; stop is idempotent per request id and preserves external sources.

- [ ] **Step 1: Write failing Python and controller tests**

```python
async def test_regeneration_failure_keeps_previous_qa_active(...):
    # make retrieval raise after preparing regenerate_of
    # assert UPDATE active=false was never committed

async def test_stop_request_rejects_oversized_partial_answer(client):
    response = client.post('/api/ask/stop', json={'question': 'q', 'partial_answer': 'x' * 20001})
    assert response.status_code == 422
```

```ts
it('stop sends ext_sources and a stable request id', async () => {
  await result.current.stop()
  expect(stopAsk).toHaveBeenCalledWith(expect.objectContaining({ ext_sources: [], request_id: expect.any(String) }))
})
```

- [ ] **Step 2: Run them and verify they fail**

Run: `uv run pytest -q -s tests/test_answer.py tests/test_ask_stop_endpoint.py && npm test -- --run frontend/src/lib/useAskController.test.ts`

Expected: existing code deactivates before replacement and accepts oversized stop payloads; the controller omits external sources.

- [ ] **Step 3: Implement atomic version replacement and bounded stop input**

```python
async def persist_replacement_and_deactivate(...):
    async with SessionFactory() as session:
        async with session.begin():
            # INSERT new qa row first
            # then UPDATE the target version or edit suffix with the same transaction
```

Pass pending replacement metadata through answer generation and execute the state update only after successful insert. Add `request_id` to `qa_log` only through an idempotency lookup compatible with existing rows, validate UUIDs, and bound question/answer/list fields with `Field(max_length=...)` and `max_length` list constraints. Send `ext_sources` from the controller.

- [ ] **Step 4: Run targeted backend and frontend tests**

Run: `uv run pytest -q -s tests/test_answer.py tests/test_ask_regenerate_edit_forwarding.py tests/test_ask_stop_endpoint.py && npm test -- --run frontend/src/lib/useAskController.test.ts`

Expected: replacement failure leaves old history active; duplicate stop requests return the same QA id; all tests pass.

### Task 3: Preserve route and overview-version safety

**Files:**
- Modify: `app/services/scope_router.py`
- Modify: `app/services/answer.py`
- Modify: `tests/test_scope_router.py`
- Modify: `tests/test_answer.py`

**Interfaces:**
- Consumes: original follow-up question and LLM-produced standalone query.
- Produces: a deterministic safety precheck on original input that cannot be downgraded by condensation; overview persistence accepts root/version metadata.

- [ ] **Step 1: Write failing tests**

```python
async def test_condense_cannot_downgrade_original_advice_question(monkeypatch):
    # LLM returns QUERY: 台積電展望 / ROUTE: CORPUS_QA
    _, decision = await condense_and_route('history', '我該不該買台積電？', today=date.today())
    assert decision.scope == ADVICE_RISK

async def test_overview_regeneration_persists_root_id(...):
    # run overview answer with regenerate_of
    # assert inserted row and done payload carry the old root id
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest -q -s tests/test_scope_router.py tests/test_answer.py`

Expected: condensed query is accepted as corpus QA and overview inserts no root id.

- [ ] **Step 3: Implement deterministic precedence and root propagation**

```python
original_pre = _safety_precheck(question)
if original_pre is not None:
    return question, _decision(original_pre)
```

Extend `_answer_overview()` with `root_qa_id` and version-count inputs; pass them to `_log_qa()` and its done payload on both normal and fallback paths.

- [ ] **Step 4: Run route and answer tests**

Run: `uv run pytest -q -s tests/test_scope_router.py tests/test_answer.py`

Expected: all tests pass.

### Task 4: Bound post-answer and rerank resource use; correct analytics

**Files:**
- Modify: `app/services/retrieval_pipeline.py`
- Modify: `app/services/rerank.py`
- Modify: `app/services/answer.py`
- Modify: `app/services/followups.py`
- Modify: `scripts/analyze_qa_log.py`
- Modify: `tests/test_retrieval_pipeline.py`
- Modify: `tests/test_rerank.py`
- Modify: `tests/test_followups.py`
- Modify: `tests/test_analyze_qa_log.py`

**Interfaces:**
- Produces: rerank queue has bounded concurrent work and timeout; tail candidates retain a score scale compatible with selection; follow-ups do not consume ask admission slots; metrics distinguish active completed rows.

- [ ] **Step 1: Write failing tests**

```python
def test_rerank_tail_keeps_original_recall_scale(...):
    # low head rerank scores and high tail fused scores
    assert selected_report_ids_after_rerank == expected_tail_ids

async def test_followups_timeout_returns_empty(monkeypatch):
    # stream never yields
    assert await generate_followups('q', 'a', timeout=0.01) == []
```

Add a SQL assertion that analytics queries include `active` and exclude `stopped` by default.

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest -q -s tests/test_rerank.py tests/test_retrieval_pipeline.py tests/test_followups.py tests/test_analyze_qa_log.py`

Expected: tail scores are compressed, followups have no explicit timeout, and analytics include inactive/stopped rows.

- [ ] **Step 3: Implement bounded work and metric filtering**

```python
# rerank.py: retain original tail `(tier, fused, row)` values
# retrieval_pipeline.py: await a bounded rerank semaphore with asyncio.wait_for
# followups.py: pass a short explicit timeout to stream_completion
# answer.py: schedule followups outside the ask admission generator or release admission before waiting
# analyze_qa_log.py: add `WHERE active AND stopped IS NOT TRUE`
```

- [ ] **Step 4: Run resource and analytics tests**

Run: `uv run pytest -q -s tests/test_rerank.py tests/test_retrieval_pipeline.py tests/test_followups.py tests/test_analyze_qa_log.py`

Expected: all tests pass and original tail candidates remain eligible.

### Task 5: Final regression verification

**Files:**
- Verify only; no new production files.

- [ ] **Step 1: Inspect the complete diff**

Run: `git diff --check && git diff main --stat && git diff main -- app/services/answer.py web/server.py frontend/src`

Expected: no whitespace errors and only intended files changed.

- [ ] **Step 2: Run backend and frontend checks**

Run: `uv run pytest -q -s && npm test && npm run build`

Expected: tests and build pass. If the full Python suite exceeds the host timeout, record the completed focused suites and the exact timeout constraint.

- [ ] **Step 3: Commit independently buildable groups**

```bash
git add app/services/answer.py web/server.py tests/test_answer.py tests/test_ask_stop_endpoint.py
git commit -m "fix(問答): 保護版本切換與停止流程"
```

Then create separate Conventional Commits for frontend state/build and retrieval/analytics only after their individual tests pass.
