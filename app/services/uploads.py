"""研報上傳（`research.report_upload`，revision 0008）的狀態與失敗類別詞彙。

本模組**只放詞彙**（常數與純判斷），不放 worker、收檔或審核邏輯：那些在後續的 PR 落地時各自
呼叫這裡，讓狀態字串只有一份定義。

- `STATES` 與 0008 的 `state` CHECK **逐字一致**（`tests/test_uploads_vocab.py` 對 migration 原文比對）；
  改了要寫新 revision 並重生 `db/expected_constraints.txt`。
- `FAILURE_KINDS` 對應 `failure_kind` 欄，DB **刻意無 CHECK**（比照 review_state）：新增失敗類別
  只改這裡，不必寫 revision。寫入端一律從這裡取值，不手寫字串。
- `ACTIVE_STATES` 與 0008 的 partial unique index `idx_report_upload_active_hash` 的條件逐字一致：
  同一個 file_hash 同時最多一筆處於這些狀態。

草稿的**可見性**不看這張表，看 `report_visibility.publication`（`app/services/visibility.py`）；
兩者的一致性（`state='draft'` 若且唯若 `publication='draft'`）由 `scripts/db_audit.py` 稽核。
"""

from __future__ import annotations

# ── 狀態 ────────────────────────────────────────────────────────────────
STATE_QUARANTINED = "quarantined"  # web 收檔完成，檔案在隔離區等掃描
STATE_SCANNING = "scanning"  # worker 已認領、送 clamd 掃描中
STATE_CLEAN = "clean"  # 掃描通過，等取 LLM 鎖入庫
STATE_INFECTED = "infected"  # 掃到病毒（終態；證據保留期滿清除，metadata 永久保留）
STATE_BLOCKED = "blocked"  # 決定性的掃描錯誤重試用盡（終態，視同攔截）
STATE_PROCESSING = "processing"  # 抽字、標註、入庫中
STATE_DRAFT = "draft"  # 已入庫但未發布：所有使用者讀取路徑看不到
STATE_FAILED = "failed"  # 處理失敗（原因在 failure_kind）
STATE_DUPLICATE = "duplicate"  # 認領時語料已有同 hash（例如 NAS 先送到）；不碰 visibility
STATE_PUBLISHED = "published"  # 管理員已發布
STATE_REJECTED = "rejected"  # 管理員退回（必填原因）；寬限期後由 worker 清除語料與檔案

STATES: tuple[str, ...] = (
    STATE_QUARANTINED, STATE_SCANNING, STATE_CLEAN, STATE_INFECTED, STATE_BLOCKED,
    STATE_PROCESSING, STATE_DRAFT, STATE_FAILED, STATE_DUPLICATE, STATE_PUBLISHED, STATE_REJECTED,
)

# 進行中：同一個 file_hash 同時最多一筆（partial unique index）。
ACTIVE_STATES: tuple[str, ...] = (STATE_QUARANTINED, STATE_SCANNING, STATE_CLEAN, STATE_PROCESSING, STATE_DRAFT)

# 處理中（還在佔用掃描與入庫的產能）：全站上限 `UPLOAD_MAX_IN_FLIGHT` 數的是這組。draft 已處理完、等人審，不算。
IN_FLIGHT_STATES: tuple[str, ...] = (STATE_QUARANTINED, STATE_SCANNING, STATE_CLEAN, STATE_PROCESSING)

# 不會再自動前進的狀態（published 仍可被隱藏，但那是 report_visibility 的事）。
TERMINAL_STATES: tuple[str, ...] = (
    STATE_INFECTED, STATE_BLOCKED, STATE_DUPLICATE, STATE_PUBLISHED, STATE_REJECTED,
)

# 可被管理員退回的狀態；scanning／processing 中退回回 409（worker 正握著它）。
REJECTABLE_STATES: tuple[str, ...] = (STATE_DRAFT, STATE_QUARANTINED, STATE_CLEAN, STATE_FAILED)

# ── 失敗類別（failure_kind；DB 無 CHECK）──────────────────────────────────
FAILURE_EXTRACT_ERROR = "extract_error"
FAILURE_EXTRACT_TIMEOUT = "extract_timeout"
FAILURE_SCANNED = "scanned"  # 掃描影像 PDF，抽不出文字
FAILURE_ADMIN_FILE = "admin_file"  # 檔名判定為行政文件（parse_filename 的 is_admin）
FAILURE_NOT_RESEARCH = "not_research"  # 標註判定非研究或沒有 market
FAILURE_ACTIVE_CONTENT = "active_content"  # /JavaScript、/Launch、/EmbeddedFile、/XFA、/RichMedia
FAILURE_ENCRYPTED = "encrypted"
FAILURE_TOO_MANY_PAGES = "too_many_pages"
FAILURE_TAG_FAILED = "tag_failed"
FAILURE_TAG_BLOCKED = "tag_blocked"  # 內容審查擋下
FAILURE_TAG_TRUNCATED = "tag_truncated"
FAILURE_INGEST_ERROR = "ingest_error"
FAILURE_HASH_MISMATCH = "hash_mismatch"  # 搬正前重算 SHA256 與 DB 不符（TOCTOU）
FAILURE_LLM_BREAKER = "llm_breaker"  # 批次斷路器觸發：延後（留在 clean），不算失敗、不是終態

FAILURE_KINDS: tuple[str, ...] = (
    FAILURE_EXTRACT_ERROR, FAILURE_EXTRACT_TIMEOUT, FAILURE_SCANNED, FAILURE_ADMIN_FILE, FAILURE_NOT_RESEARCH,
    FAILURE_ACTIVE_CONTENT, FAILURE_ENCRYPTED, FAILURE_TOO_MANY_PAGES, FAILURE_TAG_FAILED, FAILURE_TAG_BLOCKED,
    FAILURE_TAG_TRUNCATED, FAILURE_INGEST_ERROR, FAILURE_HASH_MISMATCH, FAILURE_LLM_BREAKER,
)

# failed 之中可由管理員重試（轉回 clean）的類別；其餘重跑也不會變。
RETRYABLE_FAILURE_KINDS: tuple[str, ...] = (FAILURE_TAG_FAILED, FAILURE_INGEST_ERROR, FAILURE_EXTRACT_TIMEOUT)


def is_active(state: str) -> bool:
    return state in ACTIVE_STATES


def is_retryable_failure(failure_kind: str | None) -> bool:
    return failure_kind in RETRYABLE_FAILURE_KINDS
