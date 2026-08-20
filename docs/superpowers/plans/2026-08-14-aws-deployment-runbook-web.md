# AWS 部署操作手冊與網頁簡報中心 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可直接以 Chrome／Edge `file://` 開啟的單一離線 HTML，提供八階段 AWS 部署 Runbook、版本化本機進度，以及兩份共 27 頁的原生 HTML 簡報播放器。

**Architecture:** 開發來源放在 `tools/aws_runbook/`，以純 CSS、UMD 格式原生 JavaScript 與結構化資料分離責任，再由 Python 標準函式庫建置器內嵌成根目錄單一 HTML。核心狀態轉換保持純函式並以 Node 內建測試驗證；Python 契約測試驗證輸出完整性、安全與來源頁數；最後以瀏覽器在 `file://` 模式完成桌面、手機與互動驗收。

**Tech Stack:** HTML5、CSS3、原生 JavaScript（ES2020、UMD）、Python 3.11 標準函式庫、Node.js `node:test`、pytest、Chrome／Edge `file://`。

**Spec:** `docs/superpowers/specs/2026-08-14-aws-deployment-runbook-web-design.md`

## Global Constraints

- 最終交付物固定為根目錄 `AWS部署操作手冊_2026-08.html`，不得依賴後端、npm runtime、CDN 或網路服務。
- 除 AWS Console 與官方文件外，閱讀、搜尋、勾選、備註、匯入／匯出及簡報播放必須可在 `file://` 運作。
- 不整合現有 React 前端或 FastAPI 路由，不執行 Terraform、AWS CLI、SQL 或任何破壞性指令。
- 不保存或內嵌 AWS、Anthropic、Claude、資料庫憑證、Terraform state 或 `.env` 內容。
- 兩份來源簡報必須依原頁序完整重建 16＋11＝27 頁；內容、數字、關係與來源標記不得濃縮。
- 視覺採低彩度淺灰藍、單一藍色操作色、綠色完成狀態、克制玻璃與陰影；不使用大面積多色漸層或卡片網格堆疊。
- 所有互動具鍵盤焦點與可辨識標籤，顏色不是唯一狀態提示，並支援 `prefers-reduced-motion`。
- 匯入資料一律以資料處理並透過 `textContent`／表單值渲染，不得以 `innerHTML` 執行外部字串。
- Terraform state 指令採 S3 backend `use_lockfile = true`；不得推薦已棄用的 DynamoDB locking 作為新部署預設。

---

### Task 1: 建立可重現的單檔建置骨架與契約測試

**Files:**
- Create: `tools/aws_runbook/build.py`
- Create: `tools/aws_runbook/shell.html`
- Create: `tools/aws_runbook/styles.css`
- Create: `tools/aws_runbook/core.js`
- Create: `tools/aws_runbook/content.js`
- Create: `tools/aws_runbook/app.js`
- Create: `tests/test_aws_runbook_artifact.py`
- Create: `AWS部署操作手冊_2026-08.html`

**Interfaces:**
- Consumes: UTF-8 source fragments in `tools/aws_runbook/`.
- Produces: `build(output_path: Path) -> Path`; HTML markers `/*__STYLES__*/`, `/*__CORE__*/`, `/*__CONTENT__*/`, `/*__APP__*/` are replaced exactly once.

- [ ] **Step 1: Write the failing artifact contract test**

```python
from pathlib import Path

from tools.aws_runbook.build import build


def test_build_emits_one_self_contained_html(tmp_path: Path) -> None:
    output = build(tmp_path / "runbook.html")
    text = output.read_text(encoding="utf-8")
    assert "<!doctype html>" in text.lower()
    assert "/*__STYLES__*/" not in text
    assert "/*__CORE__*/" not in text
    assert "<link rel=" not in text
    assert "<script src=" not in text
```

- [ ] **Step 2: Run the narrow test and confirm it fails because the builder does not exist**

Run: `uv run pytest -q tests/test_aws_runbook_artifact.py::test_build_emits_one_self_contained_html`

- [ ] **Step 3: Implement deterministic fragment assembly**

```python
def build(output_path: Path) -> Path:
    html = (SOURCE_DIR / "shell.html").read_text(encoding="utf-8")
    replacements = {
        "/*__STYLES__*/": (SOURCE_DIR / "styles.css").read_text(encoding="utf-8"),
        "/*__CORE__*/": (SOURCE_DIR / "core.js").read_text(encoding="utf-8"),
        "/*__CONTENT__*/": (SOURCE_DIR / "content.js").read_text(encoding="utf-8"),
        "/*__APP__*/": (SOURCE_DIR / "app.js").read_text(encoding="utf-8"),
    }
    for marker, value in replacements.items():
        if html.count(marker) != 1:
            raise ValueError(f"expected one marker: {marker}")
        html = html.replace(marker, value)
    output_path.write_text(html, encoding="utf-8", newline="\n")
    return output_path
```

- [ ] **Step 4: Add semantic shell landmarks and empty source modules, then build the root artifact**

The shell must contain `header`, `nav`, `main`, a live status region, Runbook and presentation roots, a modal root, and no external resources.

- [ ] **Step 5: Run the artifact test and inspect the generated diff**

Run: `uv run pytest -q tests/test_aws_runbook_artifact.py`

### Task 2: 實作純函式狀態核心與版本化持久化

**Files:**
- Modify: `tools/aws_runbook/core.js`
- Create: `tools/aws_runbook/core.test.cjs`
- Modify: `tools/aws_runbook/app.js`

**Interfaces:**
- Consumes: `stepIds: string[]`, unknown imported JSON, browser storage adapter.
- Produces: `createDefaultState()`, `normalizeState(raw, stepIds)`, `computeProgress(stepIds, completedIds)`, `matchesStep(step, query, filter, completedIds)`, `serializeState(state)`, `safeStorage(storage)` on `globalThis.RunbookCore` and `module.exports`.

- [ ] **Step 1: Write failing Node tests for defaults, corrupt imports, unknown IDs and progress**

```javascript
test('normalizeState drops unknown step ids and preserves valid notes', () => {
  const state = core.normalizeState({schemaVersion: 1, completed: ['iam', 'bad'], notes: {iam: 'ok'}}, ['iam']);
  assert.deepEqual(state.completed, ['iam']);
  assert.equal(state.notes.iam, 'ok');
});

test('computeProgress reports exact totals', () => {
  assert.deepEqual(core.computeProgress(['a', 'b'], ['b']), {done: 1, total: 2, percent: 50});
});
```

- [ ] **Step 2: Run `node --test tools/aws_runbook/core.test.cjs` and confirm failures**

- [ ] **Step 3: Implement the minimal pure state functions and a non-throwing storage adapter**

Unknown schema versions must return safe defaults plus `{backupRaw}`; storage failures must return `{available: false}` without interrupting the UI.

- [ ] **Step 4: Run Node tests and confirm all state cases pass**

- [ ] **Step 5: Wire app startup, debounced save, scroll/page restoration and non-blocking storage warnings**

Persist under `report-mark.aws-runbook.v1`; save only schema version, completed IDs, notes, location, expanded phases, selected deck and per-deck slide indexes.

### Task 3: 建立八階段可執行 Runbook 內容與導覽

**Files:**
- Modify: `tools/aws_runbook/content.js`
- Modify: `tools/aws_runbook/app.js`
- Modify: `tools/aws_runbook/styles.css`
- Modify: `tests/test_aws_runbook_artifact.py`

**Interfaces:**
- Consumes: approved design sections 6–9 and project deployment surface under `deploy/`, `db/schema.sql`, `.env.example`.
- Produces: `RunbookContent.phases` with exactly 8 phases and stable steps containing `id`, `phaseId`, `title`, `purpose`, `prerequisites`, `files`, `commands`, `consoleChecks`, `acceptance`, `failures`, `rollback`, `costImpact`, `risk`.

- [ ] **Step 1: Add failing Python contracts for phase count, stable IDs and every required field**

```python
def test_artifact_contains_all_runbook_contract_fields(built_html: str) -> None:
    assert built_html.count('phaseId:') >= 8
    for field in ("purpose", "prerequisites", "commands", "consoleChecks", "acceptance", "failures", "rollback", "costImpact", "risk"):
        assert f"{field}:" in built_html
```

- [ ] **Step 2: Define concrete deployment steps for account safety, Terraform bootstrap, VPC, RDS migration, ECS/ALB, ingestion, QA inference, monitoring and cutover**

Each command record is `{label, code}`; potentially destructive commands carry `risk: "destructive"` and explicit confirmation text. The Terraform bootstrap step includes S3 versioning, KMS encryption and `use_lockfile = true`.

- [ ] **Step 3: Render overview, phase navigation and step detail with semantic buttons and disclosure state**

Use DOM construction and `textContent`; code blocks receive a copy button with Clipboard API fallback to selection.

- [ ] **Step 4: Implement search plus filters `all`, `todo`, `done`, `risk` using `matchesStep`**

Search includes titles, purposes, command text and failure messages. Empty results show a reset action and do not alter stored completion state.

- [ ] **Step 5: Run Node and Python contract tests**

Run: `node --test tools/aws_runbook/core.test.cjs`

Run: `uv run pytest -q tests/test_aws_runbook_artifact.py`

### Task 4: 完成進度、備註、匯出／匯入與安全重設

**Files:**
- Modify: `tools/aws_runbook/app.js`
- Modify: `tools/aws_runbook/styles.css`
- Modify: `tools/aws_runbook/core.js`
- Modify: `tools/aws_runbook/core.test.cjs`

**Interfaces:**
- Consumes: normalized state and `RunbookContent.phases`.
- Produces: exact overall/phase progress, note editor, JSON download, JSON file import, two-step reset confirmation.

- [ ] **Step 1: Add failing tests for serialized JSON, invalid types and HTML-like note strings remaining inert data**

```javascript
test('serializeState round trips note text without executing or transforming it', () => {
  const raw = core.serializeState({...core.createDefaultState(), notes: {a: '<img onerror=alert(1)>'}});
  assert.equal(JSON.parse(raw).notes.a, '<img onerror=alert(1)>');
});
```

- [ ] **Step 2: Implement export with filename `report-mark-aws-progress-YYYY-MM-DD.json` and import validation before replacement**

Invalid import leaves current state untouched and announces the exact validation error in the live region.

- [ ] **Step 3: Implement notes autosave and phase/overall progress updates without full page reload**

- [ ] **Step 4: Implement modal reset confirmation with focus trap, Escape close and focus restoration**

- [ ] **Step 5: Run core and artifact tests**

### Task 5: 重建兩份來源簡報的 27 頁結構化內容

**Files:**
- Modify: `tools/aws_runbook/content.js`
- Create: `tools/aws_runbook/source-deck-audit.txt`
- Modify: `tests/test_aws_runbook_artifact.py`

**Interfaces:**
- Consumes: `AWS上雲架構與完整成本評估_正式版_2026-08-10.pptx` and `問答推論成本效益評估_GPU明細版_2026-08-10.pptx`, including every slide textbox and `[Sources]` note.
- Produces: `RunbookContent.decks` with deck IDs `aws-tco` and `gpu-cost`, exact slide counts 16 and 11, and slide objects `{id, number, title, kicker, layout, blocks, sourceLabel, sourceDetails}`.

- [ ] **Step 1: Add failing tests requiring deck IDs, slide counts, unique slide IDs and key figures**

```python
def test_artifact_contains_complete_decks(built_html: str) -> None:
    assert 'id: "aws-tco"' in built_html
    assert 'id: "gpu-cost"' in built_html
    assert built_html.count('deckId: "aws-tco"') == 16
    assert built_html.count('deckId: "gpu-cost"') == 11
    for figure in ("576,305", "NT$23.2–47.6K", "p95 < 3.5 秒", "7–13 工程日"):
        assert figure in built_html
```

- [ ] **Step 2: Inventory all 27 source slides in `source-deck-audit.txt`**

For each slide record source slide number, visible title, all metrics/tables, chart scale, status label (`現況實測`, `規劃估算`, `導入目標`, `內部驗收 gate`) and notes source lines.

- [ ] **Step 3: Encode slides 1–16 from the AWS TCO deck without dropping visible figures or table rows**

- [ ] **Step 4: Encode slides 1–11 from the GPU detail deck without dropping visible figures or table rows**

- [ ] **Step 5: Run the deck contract test and compare the content audit against the source NDJSON inspection**

### Task 6: 實作 16:9 簡報播放器與專用頁型

**Files:**
- Modify: `tools/aws_runbook/app.js`
- Modify: `tools/aws_runbook/styles.css`
- Modify: `tools/aws_runbook/core.js`
- Modify: `tools/aws_runbook/core.test.cjs`

**Interfaces:**
- Consumes: `RunbookContent.decks` and persisted per-deck page indexes.
- Produces: deck switcher, thumbnail rail, responsive 16:9 stage, previous/next/Home/End navigation, `F` fullscreen, Escape handling, touch swipe and expandable sources.

- [ ] **Step 1: Add failing tests for clamped slide indexes and per-deck page restoration**

```javascript
test('clampSlide keeps navigation within deck bounds', () => {
  assert.equal(core.clampSlide(-1, 11), 0);
  assert.equal(core.clampSlide(11, 11), 10);
});
```

- [ ] **Step 2: Implement presentation state transitions, excluding key events originating in inputs/textareas**

- [ ] **Step 3: Render layout variants for title, metrics, architecture flow, comparison, timeline, chart, table and decision pages**

Charts use semantic HTML/CSS with explicit values and accessible text summaries; no raster slide screenshots are embedded.

- [ ] **Step 4: Implement fullscreen with API fallback, swipe threshold and mobile page selector**

- [ ] **Step 5: Run core and artifact tests, then rebuild the root HTML**

### Task 7: 完成視覺系統、響應式與無障礙契約

**Files:**
- Modify: `tools/aws_runbook/styles.css`
- Modify: `tools/aws_runbook/shell.html`
- Modify: `tools/aws_runbook/app.js`
- Modify: `tests/test_aws_runbook_artifact.py`

**Interfaces:**
- Consumes: semantic Runbook and player markup.
- Produces: desktop sidebar shell, mobile top navigation, low-saturation glass tokens, visible focus, reduced-motion mode and overflow-safe tables/code.

- [ ] **Step 1: Add failing static checks for viewport, reduced motion, focus-visible, ARIA live region and no external font/CDN**

- [ ] **Step 2: Define exact design tokens and typography scale**

Use system font stack `-apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang TC", "Microsoft JhengHei", sans-serif`; background `#eef3f8`; action `#1769e0`; completion `#14845c`; 1 px translucent borders; radii 12/16/20 px.

- [ ] **Step 3: Implement desktop composition at 1440×900 and presentation canvas scaling**

- [ ] **Step 4: Implement mobile composition at 390×844, hiding thumbnail rail in favor of a page select and preserving 44 px targets**

- [ ] **Step 5: Add skip link, landmark labels, focus management, status text and non-color risk/completion labels**

- [ ] **Step 6: Run static tests and rebuild the artifact**

### Task 8: 安全掃描、瀏覽器驗收與交付紀錄

**Files:**
- Modify: `tests/test_aws_runbook_artifact.py`
- Create: `tools/aws_runbook/qa-ledger.txt`
- Modify: `AWS部署操作手冊_2026-08.html`

**Interfaces:**
- Consumes: final built HTML.
- Produces: passing automated checks and a QA ledger covering function, content, visual, accessibility and security acceptance.

- [ ] **Step 1: Add final static security tests**

Assert absence of AWS access key patterns, Anthropic API key patterns, `.env` assignments, external script/style/image URLs, `eval(`, `new Function(` and imported-data `innerHTML` paths.

- [ ] **Step 2: Run all targeted automated checks**

Run: `node --test tools/aws_runbook/core.test.cjs`

Run: `uv run pytest -q tests/test_aws_runbook_artifact.py`

Run: `uv run ruff check tools/aws_runbook/build.py tests/test_aws_runbook_artifact.py`

- [ ] **Step 3: Open the final artifact directly with a `file:///` URL and execute the core workflow**

Verify Runbook search/filter, completion, notes, reload persistence, export/import rejection, reset confirmation, both deck switches, 27-page reachability, keyboard navigation and fullscreen fallback.

- [ ] **Step 4: Capture and inspect desktop 1440×900 and mobile 390×844 screenshots**

Record at least five concrete comparisons in `qa-ledger.txt`: first viewport hierarchy, typography, palette/glass treatment, presentation 16:9 fit, table/code overflow, mobile navigation and focus states. Fix all material mismatches before continuing.

- [ ] **Step 5: Run the full repository-relevant checks**

Run: `uv run pytest -q tests/test_docs_contract.py tests/test_aws_runbook_artifact.py`

Run: `git diff --check`

- [ ] **Step 6: Inspect final `git diff`, confirm generated HTML matches sources, and prepare the handoff without committing unrelated local files**

