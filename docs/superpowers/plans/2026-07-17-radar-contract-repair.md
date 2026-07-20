# 觀點頁完整契約修復實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓觀點（Radar）頁面的 API、聚合語意、前端狀態與行動版呈現遵守同一份可測試契約，並修正已驗證的資料錯置、窗期競態、不可比較資訊遺失、事件截斷與 pending 狀態錯誤。

**Architecture:** 採向後相容的加法契約：保留既有三支 Radar API 與 scalar 欄位，新增明確 metadata、事件分頁端點與狀態欄位。後端以單一 broker identity、穩定時間排序及窗期 baseline 聚合；前端以 TanStack Query 的取消訊號與 query key 隔離狀態，所有 UI 只呈現當前查詢資料。先以純函式測試鎖定資料語意，再接 API、React 元件與 Playwright。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、SQLAlchemy/PostgreSQL、pytest、React 19、TypeScript、TanStack Query、Zod、Vitest、Playwright。

## 全域約束

- 所有 shell 指令都使用 `/home/kashionz/.local/bin/rtk` 前綴。
- 每項修復遵守 RED → GREEN → REFACTOR；先確認新測試會因正確原因失敗。
- 不修改或提交 `.env*`、`data/`、截圖、`node_modules/` 與其他既有未追蹤檔案。
- API 改動須讓「舊前端＋新後端」安全；新前端對 rollout 欄位使用 optional/nullish fallback。
- 有限窗期排除 `report_date IS NULL`；`all` 才能納入無日期資料。
- 券商身份統一為 `COALESCE(NULLIF(btrim(r.source), ''), NULLIF(btrim(s.broker), ''))`，且 SQL select/filter/count 與 Python 聚合必須一致。
- 同日排序統一為 `(report_date, created_at, id)`；重新擷取不可覆寫首次 `created_at`。
- EPS metadata 永遠來自 EPS 本身，禁止沿用 target price 幣別。
- 所有 change/event 必須可追溯到 report 與對應 evidence；不可比較時要回傳明確原因。

---

## Task 1：資料身份、日期與目錄契約

**Files:**

- Modify: `app/services/radar/types.py`
- Modify: `app/services/radar/queries.py`
- Modify: `app/services/radar/compute.py`
- Modify: `scripts/extract_signals.py`
- Modify: `tests/test_radar_queries.py`
- Modify: `tests/test_radar_compute.py`
- Modify: `tests/test_extract_signals_sql.py`

- [ ] **Step 1: 新增失敗測試，鎖定 null broker、無日期與同日排序**

  在 `tests/test_radar_compute.py` 新增：

  ```python
  def test_unattributed_signals_do_not_form_consensus_or_events(): ...
  def test_finite_window_excludes_undated_signal(): ...
  def test_all_window_keeps_undated_signal(): ...
  def test_same_day_latest_uses_created_at_before_uuid(): ...
  ```

  斷言 `broker=None` 或空白不形成共識、券商列或事件；30/90/180 天不納入無日期訊號；`all` 保留；同日較晚 `created_at` 勝過 UUID 字典序。

- [ ] **Step 2: 新增 SQL 與重擷取失敗測試**

  在 `tests/test_radar_queries.py` 鎖定：

  ```python
  assert "COALESCE(NULLIF(btrim(r.source), ''), NULLIF(btrim(s.broker), ''))" in sql
  assert "r.stock_code = :code" in coverage_sql
  assert "ORDER BY" 包含 "s.report_date", "s.created_at", "s.id"
  ```

  在 `tests/test_extract_signals_sql.py` 斷言 conflict update 不再出現 `created_at = now()`。

- [ ] **Step 3: 執行窄測試並確認 RED**

  Run:

  ```bash
  /home/kashionz/.local/bin/rtk uv run pytest -q tests/test_radar_queries.py tests/test_radar_compute.py tests/test_extract_signals_sql.py
  ```

  Expected: 新測試因 `Signal` 缺 `created_at`、null broker 被分組、有限窗期納入無日期與 SQL 契約不一致而失敗。

- [ ] **Step 4: 實作單一 broker identity 與穩定時間欄位**

  `Signal` 新增：

  ```python
  created_at: Optional[datetime] = None
  ```

  `SIGNAL_SELECT_COLUMNS` / `parse_signal_row` 加入 `s.created_at`。所有訊號排序使用：

  ```python
  def _signal_sort_key(signal: Signal) -> tuple[date, datetime, str]:
      return (
          signal.report_date or date.min,
          signal.created_at or datetime.min,
          signal.id,
      )
  ```

  `_by_broker` 防禦性略過 `None`、空字串及純空白 broker。SQL 將 effective broker expression 用於 select、filter、count。移除 upsert 對 `created_at` 的更新。

- [ ] **Step 5: 修正窗期、coverage 與名稱來源**

  `_in_window` 對有限窗期且 `report_date is None` 回 `False`；`all` 才回 `True`。coverage/catalog 名稱只從 `research_report.stock_code/company_name` 的直接配對取得，不能把單一 `company_name` 廣播給 `stock_targets`。catalog 最終排序固定為 `latest_report_date DESC, market, instrument_code`。

- [ ] **Step 6: 執行窄測試並確認 GREEN**

  Run:

  ```bash
  /home/kashionz/.local/bin/rtk uv run pytest -q tests/test_radar_queries.py tests/test_radar_compute.py tests/test_extract_signals_sql.py
  ```

- [ ] **Step 7: 檢視並提交**

  ```bash
  /home/kashionz/.local/bin/rtk git diff
  /home/kashionz/.local/bin/rtk git add app/services/radar/types.py app/services/radar/queries.py app/services/radar/compute.py scripts/extract_signals.py tests/test_radar_queries.py tests/test_radar_compute.py tests/test_extract_signals_sql.py
  /home/kashionz/.local/bin/rtk git diff --cached
  /home/kashionz/.local/bin/rtk git commit -m "fix(radar): 統一券商歸屬與訊號時間契約" -m "排除未歸屬與有限窗期無日期訊號，並用穩定時間鍵及正確標的名稱來源避免聚合與目錄資料錯置。"
  ```

---

## Task 2：窗期基準、EPS 與可比較性契約

**Files:**

- Modify: `app/services/radar/types.py`
- Modify: `app/services/radar/schemas.py`
- Modify: `app/services/radar/scale.py`
- Modify: `app/services/radar/compute.py`
- Modify: `tests/test_radar_scale.py`
- Modify: `tests/test_radar_compute.py`

- [ ] **Step 1: 新增比較與 EPS 的失敗測試**

  新增：

  ```python
  def test_eps_group_mismatch_returns_incomparable_reason(): ...
  def test_broker_summary_eps_uses_own_metadata(): ...
  def test_eps_primary_is_order_independent_and_policy_driven(): ...
  def test_rating_movement_uses_window_baseline_not_last_hop(): ...
  def test_rating_movement_uses_pre_window_baseline(): ...
  def test_history_uses_prior_comparable_report_and_matching_evidence(): ...
  ```

  EPS primary 規則固定為：先依 broker count 最大；FY 群組優先；以 `as_of.year` 選最近且未過期 FY，若皆過期則選最新 FY；最後以 period/currency/unit 完整 key 穩定 tie-break。

- [ ] **Step 2: 執行測試並確認 RED**

  ```bash
  /home/kashionz/.local/bin/rtk uv run pytest -q tests/test_radar_scale.py tests/test_radar_compute.py
  ```

- [ ] **Step 3: 加入向後相容 schema**

  `BrokerSummary` 保留 `latest_eps_value/latest_eps_fy`，新增：

  ```python
  latest_eps_period: Optional[str] = None
  latest_eps_currency: Optional[str] = None
  latest_eps_unit: Optional[str] = None
  ```

  `BrokerSnapshot` 新增結構化 `primary_eps`；`SnapshotDiff` 新增 `from_report_id`、`has_prior_report`。`Change` / `ChangeItem` 新增穩定的 `reason_code`，保留既有 `incomparable_reason`。

- [ ] **Step 4: 實作 EPS primary 與不可比較 change**

  抽出單一 `_select_primary_eps(...)`，讓 overview、broker summary、snapshot 共用。`diff_signals` 對 FY/period/currency/unit 不同的 EPS 回：

  ```python
  Change(
      field="eps",
      direction="incomparable",
      comparable=False,
      reason_code="eps_group_mismatch",
      incomparable_reason="EPS 群組不同：FY／期間／幣別／單位無法直接比較",
      ...,
  )
  ```

  新增 `has_comparable_fields(previous, current)`；history 要往前搜尋最近一份真正可比較的報告，且 EPS evidence 以相同 group key 配對。

- [ ] **Step 5: 實作窗期 baseline movement**

  對每家 broker：current 為窗內最新可比較評等；baseline 為窗期起點前最近可比較評等，缺少時用窗內最早評等；`all` 使用全歷程最早評等。`neutral → buy → neutral` 必須算 unchanged，而非最近一跳 downgrade。

- [ ] **Step 6: 排除無證據事件並反映 partial coverage**

  event 僅保留 material change 且至少有一筆對應 evidence 的項目；rating-only 無 evidence 不可生成假事件。只要有效資料含 `extraction_status="partial"`，coverage_state 回 `partial`。

- [ ] **Step 7: 執行窄測試並提交**

  ```bash
  /home/kashionz/.local/bin/rtk uv run pytest -q tests/test_radar_scale.py tests/test_radar_compute.py
  /home/kashionz/.local/bin/rtk git diff
  /home/kashionz/.local/bin/rtk git add app/services/radar/types.py app/services/radar/schemas.py app/services/radar/scale.py app/services/radar/compute.py tests/test_radar_scale.py tests/test_radar_compute.py
  /home/kashionz/.local/bin/rtk git diff --cached
  /home/kashionz/.local/bin/rtk git commit -m "fix(radar): 補齊窗期比較與 EPS 可追溯契約" -m "改以窗期起點計算淨評等變化，統一 EPS primary metadata，並保留不可比較原因與正確證據來源。"
  ```

---

## Task 3：事件分頁與 pending API 契約

**Files:**

- Modify: `app/services/radar/schemas.py`
- Modify: `app/services/radar/compute.py`
- Modify: `app/services/radar/queries.py`
- Modify: `app/services/radar/__init__.py`
- Modify: `web/server.py`
- Modify: `tests/test_radar_compute.py`
- Modify: `tests/test_radar_queries.py`
- Modify: `tests/test_radar_api.py`
- Create: `tests/test_radar_contract.py`

- [ ] **Step 1: 新增事件頁與 broker pending 的失敗測試**

  測試 13 個事件可取得互不重疊、順序穩定的分頁；overview 只回 3 筆 preview 但 total 仍為 13。API 測試下列契約：

  ```text
  GET /api/instrument/{code}/radar/events?market=TW&window=90&limit=5&offset=0
  200 {total, limit, offset, has_more, next_offset, items}
  ```

  `limit=0/51`、`offset=-1` 回 422；instrument 無研報回 404；有研報但 signals 未產生回 200 空頁。broker 有研報但無 signal 回 200 `pending_extraction`，未知 broker 才回 404。

- [ ] **Step 2: 執行測試並確認 RED**

  ```bash
  /home/kashionz/.local/bin/rtk uv run pytest -q tests/test_radar_compute.py tests/test_radar_queries.py tests/test_radar_api.py tests/test_radar_contract.py
  ```

- [ ] **Step 3: 新增 additive response schema**

  ```python
  class RadarEventsResponse(BaseModel):
      market: str
      instrument_code: str
      window: Window
      as_of: Optional[str]
      total: int
      limit: int
      offset: int
      has_more: bool
      next_offset: Optional[int]
      items: list[EventCard]
  ```

  `RadarOverviewResponse` 新增 optional/default 的 `recent_events_has_more` 與 `recent_events_next_offset`。catalog response 同步新增 `limit/has_more/next_offset`，保留舊 `total/offset/items`。

- [ ] **Step 4: 抽出全部事件與分頁純函式**

  事件排序固定為 `(report_date DESC, broker, report_id)`；overview 取前 3 筆，端點用 `limit/offset` slice 並計算：

  ```python
  has_more = offset + len(items) < total
  next_offset = offset + len(items) if has_more else None
  ```

- [ ] **Step 5: 實作 broker coverage 與 routes**

  新增 broker coverage query，區分 instrument 不存在、broker 不存在及 signals pending。新增 events route，沿用 auth deny-by-default 並宣告 404/422 契約；所有 market/window/limit/offset 由 FastAPI 驗證。

- [ ] **Step 6: 執行 API 與契約測試並提交**

  ```bash
  /home/kashionz/.local/bin/rtk uv run pytest -q tests/test_radar_compute.py tests/test_radar_queries.py tests/test_radar_api.py tests/test_radar_contract.py
  /home/kashionz/.local/bin/rtk git diff
  /home/kashionz/.local/bin/rtk git add app/services/radar/schemas.py app/services/radar/compute.py app/services/radar/queries.py app/services/radar/__init__.py web/server.py tests/test_radar_compute.py tests/test_radar_queries.py tests/test_radar_api.py tests/test_radar_contract.py
  /home/kashionz/.local/bin/rtk git diff --cached
  /home/kashionz/.local/bin/rtk git commit -m "feat(radar): 新增事件分頁與 pending 回應契約" -m "讓事件可完整載入並保留真實總數，同時區分未知標的、未知券商與尚待擷取的空資料狀態。"
  ```

---

## Task 4：前端 schema、請求取消與分頁 lifecycle

**Files:**

- Modify: `frontend/src/lib/radarSchemas.ts`
- Modify: `frontend/src/lib/radarSchemas.test.ts`
- Modify: `frontend/src/lib/radarApi.ts`
- Create: `frontend/src/lib/radarApi.test.ts`
- Modify: `frontend/src/features/radar/useRadar.ts`
- Modify: `frontend/src/features/radar/RadarPage.test.tsx`

- [ ] **Step 1: 新增 schema/API/hook 失敗測試**

  鎖定九種 market、EPS metadata、events page、catalog pagination、Unicode URL 編碼與 `AbortSignal`。以延遲 promise 測試從 90 切到 30 天時，舊請求會 abort，且 UI 不會用 90 天 payload 標成 30 天。

- [ ] **Step 2: 執行測試並確認 RED**

  ```bash
  /home/kashionz/.local/bin/rtk npm --prefix frontend test -- --run src/lib/radarSchemas.test.ts src/lib/radarApi.test.ts src/features/radar/RadarPage.test.tsx
  ```

- [ ] **Step 3: 實作向後相容 Zod schema**

  market schema 由 `MARKET_ORDER` 產生；新增欄位先用 `.nullish()` / `.optional()`。events endpoint schema 的分頁 metadata 為 required。對 rating/thesis 固定陣列檢查長度及唯一 key，避免無效 payload 靜默進入畫面。

- [ ] **Step 4: 傳遞 AbortSignal 並移除 placeholder 汙染**

  API 函式採 trailing options：

  ```ts
  type RequestOptions = { signal?: AbortSignal }

  getInstrumentRadar(code, market, window, { signal })
  ```

  hook 的每個 `queryFn` 都使用 TanStack Query 提供的 `signal`。overview 移除 `keepPreviousData`；query key 必須包含 market/code/window。

- [ ] **Step 5: catalog 與 events 改為 infinite query**

  catalog 以 offset/next_offset 逐頁載入，不再固定只取 50 筆。events 僅在 overview 明確顯示 `has_more` 時呼叫新端點；切換 market/code/window 時以新 key 重設頁面。

- [ ] **Step 6: 執行測試並提交**

  ```bash
  /home/kashionz/.local/bin/rtk npm --prefix frontend test -- --run src/lib/radarSchemas.test.ts src/lib/radarApi.test.ts src/features/radar/RadarPage.test.tsx
  /home/kashionz/.local/bin/rtk git diff
  /home/kashionz/.local/bin/rtk git add frontend/src/lib/radarSchemas.ts frontend/src/lib/radarSchemas.test.ts frontend/src/lib/radarApi.ts frontend/src/lib/radarApi.test.ts frontend/src/features/radar/useRadar.ts frontend/src/features/radar/RadarPage.test.tsx
  /home/kashionz/.local/bin/rtk git diff --cached
  /home/kashionz/.local/bin/rtk git commit -m "fix(radar-ui): 隔離窗期資料並支援完整分頁" -m "取消過期請求、移除 placeholder 資料汙染，並讓標的目錄與事件依伺服器游標完整載入。"
  ```

---

## Task 5：前端資料追溯、狀態與互動契約

**Files:**

- Modify: `frontend/src/features/radar/InstrumentPicker.tsx`
- Modify: `frontend/src/features/radar/RadarOverview.tsx`
- Modify: `frontend/src/features/radar/RecentChanges.tsx`
- Modify: `frontend/src/features/radar/BrokerList.tsx`
- Modify: `frontend/src/features/radar/BrokerTimeline.tsx`
- Modify: `frontend/src/features/radar/DirectionTag.tsx`
- Modify: `frontend/src/features/radar/radarFormat.ts`
- Modify: `frontend/src/features/radar/RadarStates.tsx`
- Modify: `frontend/src/features/radar/RadarPage.test.tsx`
- Create: `frontend/src/features/radar/BrokerList.test.tsx`
- Create: `frontend/src/features/radar/BrokerTimeline.test.tsx`

- [ ] **Step 1: 新增 UI 失敗測試**

  測試完整九市場 chips、`maxLength=64`、空 broker 不可展開、pending「瀏覽研報」導向 `/search?q=<code>&market=<market>`、EPS 使用自身 currency/period/unit、不可比較 reason/前後值可見、所有 thesis/evidence 可展開，以及事件「查看全部／載入更多」取得全部項目。

- [ ] **Step 2: 執行測試並確認 RED**

  ```bash
  /home/kashionz/.local/bin/rtk npm --prefix frontend test -- --run src/features/radar/RadarPage.test.tsx src/features/radar/BrokerList.test.tsx src/features/radar/BrokerTimeline.test.tsx
  ```

- [ ] **Step 3: 修正 picker、pending 與事件互動**

  market chips 從共享 `MARKET_ORDER` render 並提供 `aria-pressed`；搜尋輸入設 `maxLength={64}`。pending CTA 接到 search route。事件區分「展開 preview」與「向 API 載入下一頁」，按鈕文字與實際可得數一致。

- [ ] **Step 4: 修正 EPS 與比較追溯**

  `BrokerList` 桌機/手機均使用 `latest_eps_currency`，顯示 FY/period/unit。`DirectionTag` 對 incomparable 一律標示「不可比較」，並顯示 `prev_value/curr_value/incomparable_reason`。timeline 以 `from_report_id` 指向真正基準報告，EPS evidence 配對 group，thesis 四象限不可只取第一筆 evidence。

- [ ] **Step 5: 修正空白 broker 與 partial/pending 狀態**

  前端即使收到舊後端的 null/blank broker，也必須過濾且不可產生空白可點擊列。`pending_extraction`、`partial`、`ok` 各自使用明確文案與可達操作。

- [ ] **Step 6: 執行測試並提交**

  ```bash
  /home/kashionz/.local/bin/rtk npm --prefix frontend test -- --run src/features/radar/RadarPage.test.tsx src/features/radar/BrokerList.test.tsx src/features/radar/BrokerTimeline.test.tsx
  /home/kashionz/.local/bin/rtk git diff
  /home/kashionz/.local/bin/rtk git add frontend/src/features/radar
  /home/kashionz/.local/bin/rtk git diff --cached
  /home/kashionz/.local/bin/rtk git commit -m "fix(radar-ui): 補齊觀點資料追溯與空狀態" -m "顯示 EPS 自身 metadata、不可比較原因與全部證據，並修正空券商、事件載入及 pending 導覽。"
  ```

---

## Task 6：行動版、可及性與視覺契約

**Files:**

- Modify: `frontend/src/features/radar/RadarPage.module.css`
- Modify: `frontend/src/features/radar/KeyFigures.module.css`
- Modify: `frontend/src/features/radar/ThesisCompass.module.css`
- Modify: `frontend/src/features/radar/WindowSegmented.module.css`
- Modify: other touched Radar `*.module.css`
- Modify: `frontend/src/features/radar/RadarPage.test.tsx`
- Create: `frontend/e2e/radar.spec.ts`

- [ ] **Step 1: 新增結構與 Playwright 驗收測試**

  Unit 測試鎖定 controls 的 accessible name/pressed/expanded；Playwright 在 390×844 驗證：KPI 為兩欄且品質橫跨全寬、thesis 為 2×2、底部內容不被 bottom nav 遮住、所有主要操作至少 44×44。桌機驗證窗期切換不殘留舊數據、空券商不可點、事件可載入全部。

- [ ] **Step 2: 執行測試並確認 RED**

  ```bash
  /home/kashionz/.local/bin/rtk npm --prefix frontend test -- --run src/features/radar/RadarPage.test.tsx
  /home/kashionz/.local/bin/rtk npm --prefix frontend run test:e2e -- --grep "Radar contract"
  ```

- [ ] **Step 3: 修正 responsive layout 與 safe area**

  390px 下 KPI 採兩欄、quality 全寬；thesis 保持 2×2，僅在 `max-width: 340px` 改單欄。scroll bottom padding 至少：

  ```css
  padding-bottom: calc(56px + env(safe-area-inset-bottom) + 24px);
  ```

- [ ] **Step 4: 修正 touch target 與 contrast**

  segmented controls、breadcrumb、事件 link、timeline collapse/link 以 `min-width/min-height: 44px` 提供 hit area。使用 Radar 頁面局部的可讀文字 token，確保主要/次要文字符合 WCAG AA，不改動全站未經審核的 token。

- [ ] **Step 5: 執行前端驗證並提交**

  ```bash
  /home/kashionz/.local/bin/rtk npm --prefix frontend test -- --run src/features/radar/RadarPage.test.tsx
  /home/kashionz/.local/bin/rtk npm --prefix frontend run test:e2e -- --grep "Radar contract"
  /home/kashionz/.local/bin/rtk npm --prefix frontend run build
  /home/kashionz/.local/bin/rtk npm --prefix frontend run lint
  /home/kashionz/.local/bin/rtk git diff
  /home/kashionz/.local/bin/rtk git add frontend/src/features/radar frontend/e2e/radar.spec.ts
  /home/kashionz/.local/bin/rtk git diff --cached
  /home/kashionz/.local/bin/rtk git commit -m "fix(radar-ui): 修正行動版與可及性契約" -m "讓 390px 版面遵守 KPI、論點與底部導覽規格，並補足觸控尺寸、狀態屬性與文字對比。"
  ```

---

## Task 7：全套驗證、真實瀏覽器回歸與交付

**Files:**

- Verify only unless a regression requires a scoped fix.

- [ ] **Step 1: 執行後端與前端全套測試**

  ```bash
  /home/kashionz/.local/bin/rtk uv run pytest -q
  /home/kashionz/.local/bin/rtk node --test web/static/app/*.test.mjs
  /home/kashionz/.local/bin/rtk npm --prefix frontend test -- --run
  /home/kashionz/.local/bin/rtk npm --prefix frontend run build
  /home/kashionz/.local/bin/rtk npm --prefix frontend run lint
  ```

- [ ] **Step 2: 使用 Playwright 驗證真實資料與競態**

  以開發環境驗證：TW:8299 不再出現空券商、90→30 天不顯示舊窗期資料、TSMC EPS primary 不選歷史 FY、事件總數與載入結果一致、390×844 最後一項可完整捲出 bottom nav。只保存暫存截圖，不加入 git。

- [ ] **Step 3: 最終 diff、commit 與敏感檔檢查**

  ```bash
  /home/kashionz/.local/bin/rtk git status --short
  /home/kashionz/.local/bin/rtk git diff HEAD
  /home/kashionz/.local/bin/rtk git log --oneline --decorate -8
  /home/kashionz/.local/bin/rtk git diff --check
  ```

  確認沒有 `.env`、`data/`、截圖、產出檔與使用者原有未追蹤檔進入任何 commit。
