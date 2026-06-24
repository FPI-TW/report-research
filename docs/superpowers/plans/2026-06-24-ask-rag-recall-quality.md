# 問答 RAG 更全面引用 + 守準度新近度 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓問答引用更多來源（8→15 篇）、分析更全面，同時用 tier 感知相關度下限守準度、過舊篇數上限守新近度。

**Architecture:** 全部集中在 `app/services/answer.py`：放寬規模常數、在 `build_context` 填充迴圈加兩道閘（相關度下限、過舊配額），並微調 system prompt。全 env 可調，總覽路徑（`overview.py`）不經 `build_context` 故不受影響。

**Tech Stack:** Python 3.13、`unittest`（既有測試框架，`make_row` 造假 row、`now` 可注入）。

## Global Constraints

- 繁體中文回答使用者；程式碼/識別字/路徑保持原文。
- 時間一律 UTC-aware：`datetime.now(timezone.utc)`；日期比較用既有 `_as_date()`。
- 既有 `build_context` 呼叫端（`answer_question`、既有測試）行為不可回歸；新行為由帶預設值的新 keyword 參數 / 新常數驅動。
- 全 env 可調（營運可即時回退，如 `ASK_MAX_REPORTS=8`）。
- 總覽路徑（`app/services/overview.py`）不得受影響（它不經 `build_context`）。
- 測試用 `unittest.TestCase`，跑法 `uv run python -m pytest tests/test_answer.py -q`（pytest 9.1.0 可跑 unittest 類）。
- `uv run black/ruff/mypy` 可能缺執行檔 → 缺則略過、人工比對風格、不擋提交。
- 提交用 Conventional Commits，繁中說明 what/why；只 stage 計畫明列檔案（勿 `git add -A`；有他人未追蹤 `data/`、`.playwright-cli/`）。

---

## 檔案結構

- **Modify `app/services/answer.py`**：規模常數值、新增 4 個常數、`build_context` 簽名加 4 個 keyword 參數、填充迴圈加兩道閘、`SYSTEM_PROMPT` 微調。
- **Modify `tests/test_answer.py`**：在既有 `BuildContextTests` 後新增規模/下限/過舊測試類。
- **校準/驗證（Task 3，無新碼）**：`scripts/eval_retrieval.py`、`scripts/analyze_qa_log.py`、真實 DB/LLM 端到端。

既有可重用（同檔內，勿重造）：`make_row(report_id, file_name, market, content, report_date=None, distance=0.1)`（`tests/test_answer.py`）、`_as_date()`、`_recency_factor()`、`Source`、`build_context()`（`app/services/answer.py`）。

---

### Task 1: 加碼規模常數 + prompt 微調

**Files:**
- Modify: `app/services/answer.py:35-41`（規模常數）、`app/services/answer.py:47-57`（SYSTEM_PROMPT）
- Test: `tests/test_answer.py`

**Interfaces:**
- Produces: 放寬後的模組常數 `MAX_REPORTS=15`、`MAX_PASSAGES_PER_REPORT=4`、`MAX_CONTEXT_CHARS=20000`、`ASK_DENSE_SCAN=400`、`RETRIEVAL_K=15`；`SYSTEM_PROMPT` 含「綜合多篇研報」字樣。`build_context` 邏輯本步不變（僅預設上限變大）。

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_answer.py` 的 `BuildContextTests` 類別**之後**新增：
```python
class ScaleUpDefaultsTests(unittest.TestCase):
    NOW = datetime(2026, 6, 24, tzinfo=timezone.utc)
    D = date(2026, 6, 20)  # 近期，避免新近度截斷干擾

    def test_default_max_reports_is_15(self):
        scored = [
            (0, 0.70, make_row(f"r{i}", f"{i}.pdf", "TW", f"內容{i}", self.D))
            for i in range(20)
        ]
        sources, _ = build_context(scored, now=self.NOW)
        self.assertEqual(len(sources), 15)

    def test_default_max_passages_is_4(self):
        scored = [
            (0, 0.70, make_row("r1", "甲.pdf", "TW", f"第{i}段內容", self.D))
            for i in range(6)  # 同一報告 6 段
        ]
        sources, context = build_context(scored, now=self.NOW)
        self.assertEqual(len(sources), 1)
        self.assertIn("第0段內容", context)
        self.assertIn("第3段內容", context)
        self.assertNotIn("第4段內容", context)  # 受預設 max_passages=4 限制

    def test_system_prompt_encourages_synthesis(self):
        from app.services.answer import SYSTEM_PROMPT
        self.assertIn("綜合多篇研報", SYSTEM_PROMPT)
```
（`datetime`/`date`/`timezone` 已在檔頭 import；`make_row`/`build_context` 同檔既有。）

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run python -m pytest tests/test_answer.py::ScaleUpDefaultsTests -q`
Expected: FAIL（`test_default_max_reports_is_15` 得 8、`test_default_max_passages_is_4` 第4段仍在、prompt 無「綜合多篇研報」）

- [ ] **Step 3: 放寬規模常數**

`app/services/answer.py`，把現有四行常數值改為：
```python
MAX_REPORTS = int(os.getenv("ASK_MAX_REPORTS", "15"))
MAX_PASSAGES_PER_REPORT = int(os.getenv("ASK_MAX_PASSAGES", "4"))
MAX_CONTEXT_CHARS = int(os.getenv("ASK_MAX_CONTEXT_CHARS", "20000"))
RETRIEVAL_K = int(os.getenv("ASK_RETRIEVAL_K", "15"))
```
並把 `ASK_DENSE_SCAN` 預設改為 400：
```python
ASK_DENSE_SCAN = int(os.getenv("ASK_DENSE_SCAN", "400"))
```
（僅改 `os.getenv` 的預設字串，其餘行不動。）

- [ ] **Step 4: prompt 微調**

`app/services/answer.py` 的 `SYSTEM_PROMPT`，把第 2 條：
```python
    "2. 一律用繁體中文、條理清楚地回答。\n"
```
改為：
```python
    "2. 一律用繁體中文、條理清楚地回答；參考片段較多時，請綜合多篇研報、彼此佐證後再作答，並優先採用較新的研報。\n"
```

- [ ] **Step 5: 跑測試確認通過 + 既有不回歸**

Run: `uv run python -m pytest tests/test_answer.py -q`
Expected: PASS（新 3 案 + 既有 `BuildContextTests` 等全綠）。若既有 `test_dense_scan_forwarded_from_ask_path` 失敗，確認它斷言的是常數 `ASK_DENSE_SCAN` 而非硬編碼 200；若硬編碼，該測試屬既有、本步不改它，回報 DONE_WITH_CONCERNS。

- [ ] **Step 6: Commit**

```bash
git add app/services/answer.py tests/test_answer.py
git commit -m "feat(ask): 加碼問答脈絡規模 8→15 篇/9000→20000 字/3→4 段 + 綜合 prompt"
```

---

### Task 2: 相關度下限 + 過舊篇數上限（build_context 兩道閘）

**Files:**
- Modify: `app/services/answer.py`（新增 4 常數於既有 recency 常數區之後；`build_context` 簽名加 4 keyword 參數；替換填充迴圈）
- Test: `tests/test_answer.py`

**Interfaces:**
- Consumes: `_as_date()`、`Source`（同檔既有）
- Produces:
  - 新常數 `ASK_RELEVANCE_FLOOR=0.62`、`ASK_MIN_REPORTS=3`、`ASK_STALE_AGE_DAYS=180`、`ASK_MAX_STALE_REPORTS=4`
  - `build_context(..., min_reports=ASK_MIN_REPORTS, relevance_floor=ASK_RELEVANCE_FLOOR, stale_age_days=ASK_STALE_AGE_DAYS, max_stale=ASK_MAX_STALE_REPORTS)` 新增 4 個 keyword 參數
  - 填充行為：前 `min_reports` 篇保底納入；其後純語意(tier 0)且 `best_fused < relevance_floor` 者剔除（tier≥1 一律放行）；脈絡中「年齡 > stale_age_days」的研報最多 `max_stale` 篇

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_answer.py` 的 `ScaleUpDefaultsTests` 之後新增：
```python
class RelevanceFloorTests(unittest.TestCase):
    NOW = datetime(2026, 6, 24, tzinfo=timezone.utc)
    D = date(2026, 6, 20)  # 近期，factor 高，不觸發既有極舊截斷

    def test_tier0_below_floor_excluded_beyond_min(self):
        scored = [
            (0, 0.70, make_row("r1", "1.pdf", "TW", "強1", self.D)),
            (0, 0.69, make_row("r2", "2.pdf", "TW", "強2", self.D)),
            (0, 0.68, make_row("r3", "3.pdf", "TW", "強3", self.D)),
            (0, 0.50, make_row("r4", "4.pdf", "TW", "弱4", self.D)),  # < floor
            (0, 0.45, make_row("r5", "5.pdf", "TW", "弱5", self.D)),  # < floor
        ]
        sources, _ = build_context(
            scored, now=self.NOW, min_reports=3, relevance_floor=0.62
        )
        self.assertEqual([s.report_id for s in sources], ["r1", "r2", "r3"])

    def test_min_reports_guaranteed_even_below_floor(self):
        scored = [
            (0, 0.50, make_row("r1", "1.pdf", "TW", "弱1", self.D)),
            (0, 0.48, make_row("r2", "2.pdf", "TW", "弱2", self.D)),
            (0, 0.45, make_row("r3", "3.pdf", "TW", "弱3", self.D)),
        ]
        sources, _ = build_context(
            scored, now=self.NOW, min_reports=2, relevance_floor=0.62
        )
        self.assertEqual([s.report_id for s in sources], ["r1", "r2"])

    def test_tier1_below_floor_kept(self):
        scored = [
            (1, 0.40, make_row("r4", "4.pdf", "TW", "字面命中低分", self.D)),
            (0, 0.40, make_row("r5", "5.pdf", "TW", "純語意低分", self.D)),
        ]
        sources, _ = build_context(
            scored, now=self.NOW, min_reports=0, relevance_floor=0.62
        )
        self.assertEqual([s.report_id for s in sources], ["r4"])


class StaleCapTests(unittest.TestCase):
    NOW = datetime(2026, 6, 24, tzinfo=timezone.utc)
    RECENT = date(2026, 6, 20)        # 4 天：新
    OLD = date(2025, 11, 15)          # ~221 天：過舊(>180)但非極舊(factor>0.1，不被既有截斷丟)

    def test_old_reports_capped(self):
        scored = [
            (0, 0.70, make_row("f1", "f1.pdf", "TW", "新1", self.RECENT)),
            (0, 0.69, make_row("f2", "f2.pdf", "TW", "新2", self.RECENT)),
            (0, 0.68, make_row("o1", "o1.pdf", "TW", "舊1", self.OLD)),
            (0, 0.67, make_row("o2", "o2.pdf", "TW", "舊2", self.OLD)),
            (0, 0.66, make_row("o3", "o3.pdf", "TW", "舊3", self.OLD)),
            (0, 0.65, make_row("o4", "o4.pdf", "TW", "舊4", self.OLD)),
        ]
        sources, _ = build_context(
            scored, now=self.NOW, min_reports=0, relevance_floor=0.0,
            stale_age_days=180, max_stale=2,
        )
        ids = [s.report_id for s in sources]
        self.assertEqual(len([i for i in ids if i.startswith("o")]), 2)
        self.assertIn("f1", ids)
        self.assertIn("f2", ids)

    def test_min_reports_exempt_from_stale_cap(self):
        scored = [
            (0, 0.70, make_row("o1", "o1.pdf", "TW", "舊1", self.OLD)),
            (0, 0.69, make_row("o2", "o2.pdf", "TW", "舊2", self.OLD)),
            (0, 0.68, make_row("o3", "o3.pdf", "TW", "舊3", self.OLD)),
            (0, 0.67, make_row("o4", "o4.pdf", "TW", "舊4", self.OLD)),
        ]
        sources, _ = build_context(
            scored, now=self.NOW, min_reports=3, relevance_floor=0.0,
            stale_age_days=180, max_stale=1,
        )
        self.assertEqual([s.report_id for s in sources], ["o1", "o2", "o3"])
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run python -m pytest tests/test_answer.py::RelevanceFloorTests tests/test_answer.py::StaleCapTests -q`
Expected: FAIL（`build_context() got an unexpected keyword argument 'min_reports'`）

- [ ] **Step 3: 新增 4 個常數**

`app/services/answer.py`，在既有過舊軟截斷常數（`ASK_MIN_FRESH_BEFORE_CUTOFF` 那行）**之後**新增：
```python
# 相關度下限（tier 感知，寧缺勿濫）：純語意(tier 0)研報的 best_fused 最低門檻；
# tier≥1（字面命中）一律放行。fused 分數壓縮，故此為「弱命中防護」非精準切刀。
ASK_RELEVANCE_FLOOR = float(os.getenv("ASK_RELEVANCE_FLOOR", "0.62"))
# 保底篇數：前 N 篇不受相關度/過舊閘限制，避免邊界但合理的問題被餓死。
ASK_MIN_REPORTS = int(os.getenv("ASK_MIN_REPORTS", "3"))
# 過舊篇數上限：脈絡中「年齡 > STALE_AGE_DAYS 天」的研報最多 MAX_STALE 篇，
# 把多出的槽留給較新的相關研報（與既有極舊軟截斷並存互補）。
ASK_STALE_AGE_DAYS = int(os.getenv("ASK_STALE_AGE_DAYS", "180"))
ASK_MAX_STALE_REPORTS = int(os.getenv("ASK_MAX_STALE_REPORTS", "4"))
```

- [ ] **Step 4: build_context 簽名加 4 個 keyword 參數**

把 `build_context` 的簽名（現有 `half_life_days` 參數行）擴充為：
```python
def build_context(
    scored: list[tuple[int, float, tuple]],
    *,
    max_reports: int = MAX_REPORTS,
    max_passages: int = MAX_PASSAGES_PER_REPORT,
    max_chars: int = MAX_CONTEXT_CHARS,
    now: datetime | None = None,
    half_life_days: float = RECENCY_HALF_LIFE_DAYS,
    min_reports: int = ASK_MIN_REPORTS,
    relevance_floor: float = ASK_RELEVANCE_FLOOR,
    stale_age_days: int = ASK_STALE_AGE_DAYS,
    max_stale: int = ASK_MAX_STALE_REPORTS,
) -> tuple[list[Source], str]:
```

- [ ] **Step 5: 替換填充迴圈，插入兩道閘**

把 `app/services/answer.py` `build_context` 內現有的填充迴圈區塊（從 `    sources: list[Source] = []` 到該 `for` 迴圈結束、`blocks.append(head + "\n" + "\n".join(kept))` 為止）替換為：
```python
    sources: list[Source] = []
    blocks: list[str] = []
    total = 0
    n = 0
    stale_used = 0
    for rid, info in reports:
        if n >= max_reports:
            break
        if cutoff_active and factors[rid] < ASK_STALE_FACTOR:
            continue  # 既有極舊軟截斷：有足夠新資料 → 跳過極舊報告
        rdate_d = _as_date(info["report_date"])
        is_stale = (
            rdate_d is not None and (now_date - rdate_d).days > stale_age_days
        )
        # 保底 min_reports 篇不受相關度/過舊閘限制（避免邊界但合理的問題被餓死）
        if n >= min_reports:
            # 相關度下限（tier 感知）：字面命中(tier≥1)放行，純語意需 fused≥門檻
            if info["best_tier"] < 1 and info["best_fused"] < relevance_floor:
                continue
            # 過舊配額：年齡 > stale_age_days 的研報最多 max_stale 篇
            if is_stale and stale_used >= max_stale:
                continue
        kept: list[str] = []
        for content in info["passages"]:
            if total + len(content) > max_chars:
                continue
            kept.append(content)
            total += len(content)
        if not kept:
            continue
        n += 1
        if is_stale:
            stale_used += 1
        rdate = info["report_date"]
        rdate_s = rdate.isoformat() if hasattr(rdate, "isoformat") else (rdate or None)
        sources.append(
            Source(
                n=n,
                report_id=rid,
                file_name=info["file_name"],
                market=info["market"],
                report_date=rdate_s,
            )
        )
        head = f"[{n}] 報告：{info['file_name']}"
        bits = []
        if info["market"]:
            bits.append(f"市場 {info['market']}")
        if rdate_s:
            bits.append(f"日期 {rdate_s}")
        if bits:
            head += "（" + "，".join(bits) + "）"
        blocks.append(head + "\n" + "\n".join(kept))
```
（其後的 `is_latest` 標記與 `return` 不變。）

- [ ] **Step 6: 跑測試確認通過 + 既有不回歸**

Run: `uv run python -m pytest tests/test_answer.py -q`
Expected: PASS（新 5 案 + Task 1 的 3 案 + 既有 `BuildContextTests`/其餘全綠）。

- [ ] **Step 7: Commit**

```bash
git add app/services/answer.py tests/test_answer.py
git commit -m "feat(ask): build_context 加 tier 感知相關度下限與過舊篇數上限"
```

---

### Task 3: 校準預設值 + 離線/Live 端到端驗證

**Files:**
- 無新增碼（如校準後要改預設，僅動 `app/services/answer.py` 常數預設字串）

- [ ] **Step 1: 全測試套件**

Run: `uv run python -m pytest -q`
Expected: 全綠（含 `tests/test_answer.py` 新案；`tests/test_overview.py` 若不在本分支則不影響）。

- [ ] **Step 2: 真實查詢校準相關度下限**

跑一次性探測，看廣/窄問題的 `best_fused` 分佈與「下限 0.62 會剪掉幾篇」：
```bash
uv run python -c "
import asyncio
from datetime import datetime, timezone
from app.services.db import SessionFactory
from app.services.embed import embed_query_cached
from app.services.retrieval import hybrid_search
from app.services.answer import build_context

async def probe(q):
    qvec = await asyncio.to_thread(embed_query_cached, q)
    async with SessionFactory() as s:
        scored = await hybrid_search(s, q, qvec, k=15, dense_scan=400)
    srcs, _ = build_context(scored, now=datetime.now(timezone.utc))
    print(f'{q!r}: 納入 {len(srcs)} 篇; 日期 {[s.report_date for s in srcs]}')

async def main():
    for q in ['台積電的投資展望', '美國降息對台股的影響',
              '某冷門小型股的特定併購案進度', '健身環大冒險銷售']:
        await probe(q)

asyncio.run(main())
"
```
Expected: 廣問題納入接近 15 篇且偏新；窄/冷門問題納入明顯較少（相關度下限生效，寧缺勿濫）。**判讀**：若廣問題被下限砍到遠少於 15（與 §背景 fused≈0.70 矛盾），調低 `ASK_RELEVANCE_FLOOR` 預設；若窄問題仍塞滿 15 篇弱相關，調高。改完於 `answer.py` 常數預設，重跑本步。

- [ ] **Step 3: Live LLM 端到端抽查**

對 1~2 個代表問題跑功能分支 `answer_question`，確認來源變多、答案綜合多篇、延遲可接受：
```bash
uv run python -c "
import asyncio
from app.services.answer import answer_question

async def main():
    toks=[]; kinds=[]
    async for k,p in answer_question('台積電2026年的投資展望如何'):
        kinds.append(k)
        if k=='sources': print('SOURCES:', len(p))
        elif k=='status': print('STATUS:', p)
        elif k=='token': toks.append(p)
        elif k=='done': print('DONE cited:', len(p.get('cited',[])))
    print('=== ANSWER ==='); print(''.join(toks)[:1200])

asyncio.run(main())
"
```
Expected: `SOURCES` 明顯多於舊的 ~6.5（朝 ~15）；`DONE cited` 通常 > 舊的 ~4.7；答案綜合多篇且偏新；延遲在 ~60s 量級。**清理**：抽查會寫一列 `research.qa_log`，記下 done 的 qa_id，事後刪除：
```bash
docker.exe exec -i report-mark-postgres psql -U postgres -d research -c "DELETE FROM research.qa_log WHERE id = '<qa_id>';"
```

- [ ] **Step 4: 若校準有改預設則 Commit**

```bash
git add app/services/answer.py
git commit -m "tune(ask): 依實測校準相關度下限/過舊參數預設"
```
（若 Step 2/3 判定預設無需調整，跳過本步、於報告註明「預設沿用、實測達標」。）

---

## Self-Review

**1. Spec coverage：**
- §1 加碼規模（MAX_REPORTS/PASSAGES/CHARS/DENSE_SCAN/K）→ Task 1 Step 3。✅
- §2 相關度下限（tier 感知 + 保底 min_reports）→ Task 2（常數 + 閘 + 測試 3 案）。✅
- §3 過舊篇數上限（stale_age_days/max_stale + 保底例外）→ Task 2（常數 + 閘 + 測試 2 案）。✅
- §4 填充迴圈三道閘整合順序（保底→相關度→過舊）→ Task 2 Step 5 的迴圈。✅
- §5 prompt 微調 → Task 1 Step 4。✅
- §6 相容（簽名加帶預設 keyword、總覽不受影響）→ Task 2 Step 4 全為帶預設參數；總覽不經 build_context。✅
- §測試（純函式各案 + 校準/eval + live）→ Task 1/2 單元 + Task 3 校準/驗證。✅
- §不在範圍（re-ranker/multi-query/重型 prompt/動態調整）→ 計畫未納入。✅

**2. Placeholder scan：** 無 TBD/TODO；每步含完整程式碼與指令。Task 3 的「判讀」含明確調參方向，非佔位。✅

**3. 型別/一致性：** `build_context` 新參數名 `min_reports/relevance_floor/stale_age_days/max_stale` 在 Task 2 Step 4（簽名）、Step 5（迴圈使用）、Step 1（測試呼叫）三處一致；常數名 `ASK_RELEVANCE_FLOOR/ASK_MIN_REPORTS/ASK_STALE_AGE_DAYS/ASK_MAX_STALE_REPORTS` 一致。既有 `_as_date`/`ASK_STALE_FACTOR`/`cutoff_active`/`factors` 沿用既有定義。✅

**4. 測試與既有的交互檢核：**
- 過舊測試用 `OLD=2025-11-15`（~221 天，factor≈0.18 > `ASK_STALE_FACTOR=0.1`），**刻意**避開既有極舊軟截斷，確保測的是新 stale-cap 而非舊邏輯。✅
- 相關度測試用近期日期（factor 高）避免既有截斷干擾；`min_reports=0`/`relevance_floor=0.0` 用於隔離單一機制。✅
- Task 1 的 `test_default_max_reports_is_15` 用 fused 0.70（> 預設 floor 0.62），故 Task 2 加入 floor 後此測試仍綠（不回歸）。✅
