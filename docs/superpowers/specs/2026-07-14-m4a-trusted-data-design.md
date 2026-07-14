# M4a 受信任時效資料契約 設計規格

日期：2026-07-14。藍圖：`docs/QA_REDESIGN.md` §3-A、§3-B、backlog：`docs/IMPLEMENTATION_PLAN.md` M4a。
分支：`feat/m4a-trusted-data`，自 `origin/main`（5f5ed12）切出。依賴 M4（五類路由，已在 main）。

## 目標

為 `time_sensitive` 路由建立**受信任時效資料的 adapter 契約**：核准來源 allowlist、結構化回應、資料年齡驗證、快取 TTL、速率限制與取消傳播。Claude 只能決定「是否需要時效資料」（M4 路由已完成）；**不能自由挑選網頁、不能繞過 allowlist、不能把研報或一般網搜文字當即時資料**。

關鍵安全語義：**registry 預設為空** → 執行期行為與 M4 完全相同（`TIME_SENSITIVE_UNAVAILABLE_MESSAGE` 安全婉拒）。本里程碑交付的是契約 + 驗證框架 + 接線；真實 provider（如 findb Serve API、交易所公開 API）依此契約於後續註冊，屬部署決策。

## 非目標（YAGNI）

- 不接入任何真實 provider、不發真實 HTTP 請求（測試一律 fake provider）。
- 不做多標的批次報價、歷史序列、盤中推播。
- 不改 scope_router 的分類邏輯與安全前檢。
- 不做 evidence ledger 持久化格式（M4b）；本階段時效答案的來源以 `ext_sources` 加法欄位落 `qa_log`。
- 不做前端變更：時效答案走既有 `token`／`done` 事件；婉拒走既有 `notice`。

## 設計

### 1. `app/services/trusted_market_data.py`

#### 資料結構

```python
Category = Literal["quote", "filing", "rate"]   # 報價 / 公告·財報 / 利率·政策

@dataclass(frozen=True)
class TrustedQuery:
    category: Category
    question: str           # 原始（或改寫後）問題；provider 自行解析標的
    symbol: str | None = None

@dataclass(frozen=True)
class TrustedDataPoint:
    value: str              # 顯示值（如 "1085.00"）
    unit: str | None        # 如 "TWD"
    as_of: datetime         # 資料截至時間，必須 tz-aware
    published_at: datetime | None
    url: str                # 來源 URL（必須落在 allowlist 網域）
    source_type: str        # 來源性質："exchange" | "official" | "regulator" | ...
    content_hash: str       # provider 原始 payload 的 sha256 hex
    provider: str           # provider 名稱
    category: Category
    subject: str            # 標的/主題描述（如 "台積電 2330"）

@dataclass(frozen=True)
class ProviderSpec:
    name: str
    category: Category
    allowed_domains: tuple[str, ...]  # 網域 allowlist（含子網域比對）
    max_age: timedelta                # 最大資料年齡（quote 分鐘級；filing/rate 天級）
    cache_ttl: timedelta
    timeout: float                    # provider fetch 逾時（秒）
    min_interval: float               # 速率限制：兩次真實 fetch 最小間隔（秒）
    exchange_tz: str                  # IANA 時區（如 "Asia/Taipei"），供顯示與稽核

class TrustedProvider(Protocol):
    async def fetch(self, query: TrustedQuery) -> TrustedDataPoint | None: ...

class TrustedDataUnavailable(Exception):
    """任何取得失敗的統一出口：呼叫端一律安全婉拒，不得分支繞過。reason 供 log。"""
```

#### Registry 與 facade

- `register_provider(spec, provider)` / `clear_providers()`（測試用）／`available_categories()`。每 category 至多一個 provider（v1 簡化）。
- **預設 registry 為空**；`TRUSTED_DATA_ENABLED`（env，預設 "1"）為總開關，關閉時一律 unavailable（kill-switch）。

```python
async def fetch_trusted(
    category: Category, question: str, *, symbol=None, now=None,
) -> TrustedDataPoint:
```

流程（任一步失敗 raise `TrustedDataUnavailable(reason)`）：

1. 總開關關閉 / 該 category 無 provider → unavailable。
2. 快取命中（key = `(category, norm_for_match(question))`，`now < expires_at`）→ 直接回快取點（不受速率限制）。
3. 速率限制：`now < next_allowed_at` 且快取未命中 → unavailable（不打 provider）。
4. `asyncio.wait_for(provider.fetch(query), timeout)`；逾時、任何 provider 例外、回 `None` → unavailable。**`asyncio.CancelledError` 原樣上拋**（取消傳播，不得吞成 unavailable），且不寫快取。
5. `validate_point(point, spec, now)`（純函式）：
   - `url` 必須 `https://`（或 `http://`）且 hostname 恰為 allowlist 網域或其子網域；
   - `as_of` 必須 tz-aware，且 `now - as_of <= max_age`（過舊 → stale 拒收）；
   - `value`、`source_type`、`content_hash` 非空；`content_hash` 為 64 字 hex；
   - 任一不符 → unavailable（欄位缺漏、資料過舊、來源不在 allowlist 都不得進入答案）。
6. 驗證通過 → 寫快取（`expires_at = now + cache_ttl`）、更新 `next_allowed_at = now + min_interval`，回傳。

決定性設計：快取與速率限制以 `now: datetime`（預設 `datetime.now(timezone.utc)`）判斷，測試注入固定時間即可完全決定性，不需 monkeypatch 時鐘。

#### Category 推斷（確定性）

`infer_category(question) -> Category`：關鍵詞映射——公告/財報/法說/年報/季報 → `filing`；利率/央行/Fed/升息/降息/決策會議 → `rate`；其餘（股價/報價/收盤/開盤/行情等，即路由送進來的預設）→ `quote`。推錯 category 的後果只是「找不到 provider → 安全婉拒」，不會產生錯誤資料。

### 2. `answer.py` 接線

兩個 `TIME_SENSITIVE` 分支呼叫點（首輪與續問）改走新的 `_answer_time_sensitive(...)`：

```
try:
    point = await fetch_trusted(infer_category(q), q)
except TrustedDataUnavailable:
    → 委派既有 _yield_routed_notice（文案、事件序、qa_log 寫法完全不變）
成功:
    → yield ("sources", []) → status generating → token(確定性模板答案) → done(qa_id...)
```

- 模板答案（**零 LLM、零檢索**）：呈現 `subject`、`value`+`unit`、**資料時間 `as_of`**（含交易所時區換算顯示）、發布時間、**來源性質 `source_type` 與 provider**、URL，以及「即時資料僅供參考」提示。滿足驗收「答案顯示資料時間與來源性質」。
- `qa_log`：`path="time_sensitive"`，`ext_sources` 加法擴充一筆 `{title, url, source_type, as_of, provider, content_hash}`（前端既有渲染只讀 `title`/`url`，其餘欄位忽略——相容）。
- **不呼叫 `retrieve_context`**（結構上「不得把研報當即時資料」）；取消傳播沿 async generator 天然成立。
- `off_topic` 分支與既有事件序完全不動。

### 3. `config.py`

- `trusted_data_enabled: bool`（`TRUSTED_DATA_ENABLED`，預設 "1"）。
- ProviderSpec 的 timeout/TTL/max_age 隨 provider 註冊時給定（非全域 env）；v1 不需其他 env。

## 測試

`tests/test_trusted_market_data.py`（fake provider，每 category 各一組）：

- 成功：三 category 各自 fetch 成功、答案欄位齊全。
- 過期：`as_of` 超過 `max_age` → unavailable。
- provider 失敗：例外／回 None／逾時 → unavailable。
- allowlist：URL 網域不在 allowlist（含相似網域 `evil-twse.com` 不得誤放行）→ unavailable。
- 快取 TTL：TTL 內第二次呼叫不打 provider；TTL 過後重新 fetch。
- 速率限制：min_interval 內連續 miss → 第二次不打 provider、回 unavailable。
- 取消傳播：fetch 中 cancel → `CancelledError` 上拋、無快取殘留。
- 總開關：`TRUSTED_DATA_ENABLED=0` → 有 provider 也 unavailable。

`tests/test_answer.py` 增（或 `test_answer_trusted.py`）：

- 無 provider（預設）：`time_sensitive` 問題 → 既有 notice 事件序與文案（M4 婉拒回歸）。
- 有 fake provider：答案含資料時間、來源性質；**`retrieve_context` 未被呼叫**（monkeypatch 哨兵）；`qa_log` 的 `ext_sources` 含結構化來源。
- 續問路徑（condense 判 time_sensitive）同樣走 adapter。

## 驗收清單

- [ ] 每類時效題都有 adapter 成功、過期、provider 失敗、不在 allowlist 的測試。
- [ ] 成功答案顯示資料時間與來源性質；來源結構化落 `qa_log.ext_sources`。
- [ ] 對外網頁文字沒有任何路徑可直接成為受信任時效數值（僅 `TrustedDataPoint` 結構可進入答案）。
- [ ] adapter 不可用（預設空 registry）時，M4 既有婉拒行為與測試全數成立。
- [ ] Commit：`feat(問答): 建立受信任時效資料來源契約`。
