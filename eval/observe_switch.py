"""DeepSeek 切換後的批次產出觀測（遷移 PR-16，第四版「緊急直接切換」）：零 LLM、唯讀、一次性。

claude CLI 已於 2026-09-23 失效，批次被迫直接切到 DeepSeek，**切換前閘門取消、改成切換後觀測**。
這支把「切換前 N 天的 Claude 產出」與「切換後的 DeepSeek 產出」擺在一起，依計畫第四版 §判準的
**方向**取 CI 端點、標出是否落在 D-N／D-A 容差內。**只輸出判讀，不做任何切換、不改任何設定**：
發現劣化時能做的只有修 prompt 或換 `deepseek-v4-pro`（沒有 Claude 可以退回），那是人的決定。

建議在切換後第 7 天、第 14 天各跑一次（用法見 `docs/WORKFLOW.md`「DeepSeek 切換後觀測」）。
**全庫回填（`scripts/backfill_extraction.py`、`report-mark-backfill.timer`）期間不要跑**：回填改寫全文、
重算摘錄錨點，本腳本雖然排除被回填過的研報，回填中途的篇數會一直變，兩次報告不可比。

**判讀總表全部通過不等於批次 A 觀測完成**：幻覺率、每日花費金額等零 LLM 量不到或不在用量紀錄裡的項目，
報告的「未涵蓋項目」一節逐條列出，要另外看。

## 零 LLM、唯讀

- 不 import 任何 LLM 呼叫層（`llm`／`llm_http`／`_claude_cli`／`_claude_lock`／`_llm_env`／`eval.judge`／
  問答服務層），只取 `llm_models` 的常數與白名單判斷。`tests/test_observe_switch.py` 以 AST 釘住，並確認
  `tests/test_llm_env_loading.py` 的入口掃描不會把它當成 LLM 入口（所以不需要列進 `NON_LLM_ENTRIES`）。
- DB 一律經 `SessionFactory`，單一交易、第一句 `SET TRANSACTION READ ONLY`，接著
  `relax_statement_timeout`（`SET LOCAL`），最後 rollback。不寫任何表。
- 檔案只讀：用量紀錄（`--usage-log`）、tags 快取（`--tags-dir`）、斷路器標記（`--breaker-file`），
  不存在就略過並寫進限制。三者預設都是**本 checkout** 的 `data/`：從 worktree 跑時那裡沒有生產資料，
  要明確指到部署目錄（報告與 stderr 會提示）。輸出只到 stdout 或 `--out`，`--out` 只接受 repo 外的路徑
  （本 checkout 與主 checkout 底下一律拒收，不只 `data/`，免得蓋掉已追蹤的檔案或生產資料）。
- 不取 `scripts/_claude_lock.py` 的 flock（不呼叫 LLM、不寫表），也不進 LOCKED_SCRIPTS。

## 怎麼分群（產出模型的判斷依據）

窗期：Claude 群＝`[switch_at − before_days, claude_until)`；DeepSeek 群＝`[switch_at, until)`。
`claude_until`（`--claude-until`，預設 `2026-09-23T09:05+08:00`，claude CLI 失效的時點；晚於 `switch_at`
時取 `switch_at`）到 `switch_at` 之間是**事故空窗**：CLI 已失效、DeepSeek 還沒上線，這段時間入庫的研報
下游批次全失敗，算進 Claude 群會把事故算成 Claude 的缺值，所以兩群都不收，報告註明。

依據 D-C（CLI 永久放棄）：**切換後寫入的東西只可能來自 DeepSeek**，所以時間就是主要依據，
能拿到明確的模型紀錄時以紀錄為準：

- 摘錄：`raw_payload.model`（PR-15 起才有；沒有這個鍵依 `created_at` 判）。摘錄是 DELETE＋INSERT，
  `created_at` 就是寫入時間，所以不看用量紀錄（一次切換後的 rejected 不會重寫舊列）。
- 訊號：`raw_payload.model`；沒有鍵時看用量紀錄（切換後有成功呼叫＝DeepSeek 重寫過），再退回 `created_at`。
  限制：upsert 不更新 `created_at`，PR-15 之前、又沒有用量紀錄時，切換後重擷取的舊列會被算成 Claude。
- 標題、摘要：用量紀錄（`task`＋`file_hash` 切換後的最後一次成功呼叫），沒有就依研報 `created_at`。
  限制：`research_report` 沒有標題／摘要的更新時間，切換前入庫、切換後才補的列，沒有用量紀錄時會被算成 Claude。
- 標註：用量紀錄，沒有就依入庫時間。入庫的取 `research_report.created_at`（回填
  `backfill_extraction.py` 會刷新 `extraction_log.updated_at`）；非研報沒有報告列，取 `extraction_log.updated_at`。

用量紀錄 `data/llm_usage.jsonl` 由 PR-12 起的批次寫入（`scripts/_claude_cli.py`），分群只取 `kind` 為 null
（傳輸成功）且帶 `file_hash` 的列，模型取 `model_resp`，沒有就取 `model_req`。檔案不存在時只用時間判，
報告會註明。

缺值率／產出率（應補未補、摘錄未產出）看**入庫批次**：Claude 批次＝Claude 窗期內入庫的研報，
DeepSeek 批次＝切換後入庫、且早於 `until − grace_hours` 的研報（太新的還沒輪到下游批次）。
**已填只算該批次自己的模型產出的**：標題積壓 `ORDER BY report_date DESC`，切換後會先補近 30 天
Claude 失敗的那些，拿「現在的值」算會讓 Claude 批次的填補率被 DeepSeek 的後補墊高。所以 Claude 批次裡
由 DeepSeek（用量紀錄或摘錄的 `raw_payload.model`／`created_at`）後補的值算「未填」，另列「後補」篇數。
沒有用量紀錄時標題／摘要分不出後補（限制一節會寫）。

## 指標與判讀（計畫第四版 §判準、D-N、D-A）

| 指標 | 判準 | 取哪一端 |
|---|---|---|
| 摘錄：任一方式錨定成功率（**主**，D-A） | 差值（DeepSeek − Claude）≥ −5pp | 差值的 CI 下界 |
| 摘錄產出率、訊號非 rejected 率、標題／摘要填補率（「解析成功率」，D-N） | 差值 ≥ −2pp | 差值的 CI 下界 |
| 標註：`skip_non_research` 比例、market=None 比例（批次 B：≤ 過去 30 天） | 差值 ≤ 0 | 差值的 CI 上界 |
| 用量紀錄：各 task 的 content_filter 比例（批次 A） | ≤ 1% | 比例的 Wilson CI 上界 |
| 用量紀錄：截斷（`truncated`）、401（`auth`）、402（`quota`）；斷路器標記 | 0／沒有觸發 | 計數 |
| exact 錨定率、每篇條數、殘留簡體、長度、各分布、跳過名單、`timeout_streamed`、每日 token | 觀測，不判 | — |

判讀三態：CI 那一端落在容差內＝「通過」；整條 CI 都在容差外＝「劣化」；CI 跨過容差＝「未定」（樣本不足
以下結論，另列點估計是否在容差內）。任一群沒有樣本＝「樣本不足」。

- 摘錄錨定**重算**：對 `clean_extracted(full_text)` 跑 `app/services/reading/anchor.locate_quote`（與
  `scripts/extract_takeaways.build_rows` 同一條路徑），不讀存的 `anchor_method`、不寫回。9/24 探測的
  Claude 臂讀的是**存下的** `anchor_method`，本腳本對兩群都重算，所以數字不會重現探測的 39.9%／90.9%。
  分母是有 quote 的條目。兩種研報不計入錨定率、另列篇數：
  1. **擷取後被回填過**：`extraction_log.updated_at > 摘錄 created_at`。回填（`backfill_extraction.py`）
     改寫全文後經 `store.reanchor_takeaways` 把 `text_sha256` 換成新正典文字的 sha，只看 sha 擋不到；
     但引文是 LLM 對舊文字寫的，拿新文字量會把抽取層的變化算到模型頭上。回填（含保留舊文字的
     `kept_previous`）與重新入庫都會刷新 `updated_at`，所以這條偏保守（會多排除一些沒變的）。
  2. 存的 `text_sha256` 跟現在的正典文字對不上（其他改全文的路徑）。
  CI 是以研報為單位的 bootstrap（同一篇的條目彼此相關，逐條算會低估變異）。
- 其他比例用 Wilson 區間，差值用 Newcombe（Wilson 混合）區間，都是 95%。
- 摘錄 rejected **不寫列**（`extract_takeaways.py`），所以「rejected 比例」以入庫批次為母體算「未產出率」
  （＝rejected＋失敗＋還沒跑＋他群後補）；DeepSeek 側另列跳過名單裡的 takeaway 筆數。
- 殘留簡體：`zh_hant.count_simplified(text) > 0` 的比例（與探測報告相同）。
- 標註的 market=None 分母含非研報（`not_research` 的 tags 快取讀得到就計入），小樣本下兩群差值的 CI
  很寬，常判「未定」屬預期；`skip_non_research` 同理。
- 跳過名單：`research.llm_task_failure` 裡 `first_at ≥ switch_at` 的列（換 model 時 `first_at` 會重設），
  依 task／reason 計數，並以 `llm_failures.should_skip` 算已經被跳過的篇數。
- 用量紀錄判準只看切換後（`[switch_at, until)`）`backend="http"` 的列：content_filter 的分母是「供應商
  有回應內容判斷」的呼叫（扣掉 auth／quota／config／timeout／overloaded／network）；截斷區分 `truncated`
  （`finish_reason=length`，判準 0）與 `timeout_streamed`（已吐字後碰到總期限，期限型截斷、可重放，只列觀測），
  並對照跳過名單列出「沒記入也沒有後續成功」的截斷；401／402 是 `kind` 為 `auth`／`quota` 的列
  （帳號層級錯誤會中止整批，所以通常只有一兩列）。斷路器看 `--breaker-file` 的標記：它每次跳脫覆寫、
  不會自己刪，只看得到**最後一次**，`ts` 在切換後就算觸發過。

## 用法

    uv run python eval/observe_switch.py --switch-at 2026-09-25T10:00 --dry-run   # 只印查詢（until 不檢查）
    uv run python eval/observe_switch.py --switch-at 2026-09-25T10:00             # markdown 到 stdout
    uv run python eval/observe_switch.py --switch-at 2026-09-25T10:00 --json --out /tmp/observe-d7.json

`--switch-at`／`--claude-until`／`--until` 沒帶時區時視為台北時間。退出碼：0＝報告已產出（不論判讀結果）；
2＝參數錯誤。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import random
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.services.db import SessionFactory, relax_statement_timeout  # noqa: E402
from app.services.llm_failures import FailureRecord, should_skip  # noqa: E402
from app.services.llm_models import (  # noqa: E402
    TASK_SIGNAL,
    TASK_SUMMARY,
    TASK_TAG,
    TASK_TAKEAWAY,
    TASK_TITLE,
    is_http_model,
)
from app.services.reading.anchor import locate_quote  # noqa: E402
from app.services.signal_extract import STANCE_CONSTRUCTIVENESS, THESIS_DIMENSIONS  # noqa: E402
from app.services.tagging import load_tag  # noqa: E402
from app.services.textnorm import clean_extracted  # noqa: E402
from app.services.zh_hant import count_simplified  # noqa: E402

TAIPEI = ZoneInfo("Asia/Taipei")
CLAUDE = "claude"
DEEPSEEK = "deepseek"
GROUPS = (CLAUDE, DEEPSEEK)
Z95 = 1.959963984540054

# 計畫第四版 D-N／D-A 的容差（比例，不是百分點）
TOL_PARSE = 0.02   # 解析成功率（摘錄產出、訊號非 rejected、標題／摘要填補）
TOL_ANCHOR = 0.05  # 摘錄任一方式錨定成功率
HIGHER = "higher"  # 越高越好：差值 ≥ −容差，看 CI 下界
LOWER = "lower"    # 越低越好：差值 ≤ 容差，看 CI 上界

VERDICT_PASS = "通過"
VERDICT_FAIL = "劣化"
VERDICT_UNSURE = "未定（CI 跨過容差）"
VERDICT_NO_DATA = "樣本不足"

RC_OK = 0
RC_CONFIG = 2

DEFAULT_USAGE_LOG = ROOT / "data" / "llm_usage.jsonl"
DEFAULT_TAGS_DIR = ROOT / "data" / "tags"
DEFAULT_BREAKER_FILE = ROOT / "data" / ".llm_breaker"
# claude CLI 失效的時點（2026-09-23 09:05 台北）：到切換之間是事故空窗，兩群都不收（模組 docstring「怎麼分群」）
DEFAULT_CLAUDE_UNTIL = "2026-09-23T09:05+08:00"

# 用量紀錄判準（批次 A）
CONTENT_FILTER_CAP = 0.01  # content_filter 比例 ≤ 1%（看 Wilson 上界）
# 這些 kind 表示供應商沒有對內容下判斷（帳號層級、沒吐字就逾時、過載、網路），不進 content_filter 分母
NO_CONTENT_VERDICT_KINDS = frozenset({"auth", "quota", "config", "timeout", "overloaded", "network"})
KIND_TRUNCATED = "truncated"
KIND_TIMEOUT_STREAMED = "timeout_streamed"
KIND_CONTENT_FILTER = "content_filter"
KIND_AUTH = "auth"    # 401
KIND_QUOTA = "quota"  # 402

# ── 查詢（--dry-run 原樣印出；測試以這些常數比對假 session 收到的 SQL）──────────────────

READ_ONLY_SQL = "SET TRANSACTION READ ONLY"

FAILURE_TABLE_READY_SQL = "SELECT to_regclass('research.llm_task_failure') IS NOT NULL"

# 摘錄是 DELETE＋INSERT（scripts/extract_takeaways.py），created_at 就是寫入時間，所以窗期直接套在列上。
# extraction_log.updated_at：回填（backfill_extraction.py）會刷新它，晚於摘錄 created_at＝擷取後全文被改過。
TAKEAWAY_SQL = """
SELECT t.report_id::text AS report_id, r.file_hash, t.quote, t.extraction_status, t.text_sha256,
       t.created_at, t.raw_payload->>'model' AS model, l.updated_at AS log_updated_at
FROM research.report_takeaway t
JOIN research.research_report r ON r.id = t.report_id
LEFT JOIN research.extraction_log l ON l.file_hash = r.file_hash
WHERE t.created_at >= :before_start AND t.created_at < :until
""".strip()

FULL_TEXT_SQL = """
SELECT id::text AS report_id, full_text
FROM research.research_report
WHERE id = ANY(CAST(:ids AS uuid[]))
""".strip()

# 訊號是 upsert、不更新 created_at：切換後重擷取的舊列靠 raw_payload.model 或用量紀錄撈進來。
SIGNAL_SQL = """
SELECT s.report_id::text AS report_id, r.file_hash, s.extraction_status, s.thesis_dimensions,
       s.created_at, s.raw_payload->>'model' AS model
FROM research.report_signal s
JOIN research.research_report r ON r.id = s.report_id
WHERE s.extraction_status <> 'pending'
  AND (s.created_at >= :before_start
       OR (s.raw_payload->>'model') IS NOT NULL
       OR r.file_hash = ANY(CAST(:usage_hashes AS text[])))
""".strip()

# 母體與 generate_titles／generate_summaries／extract_takeaways 的工作集一致：有全文、非「明確非研報」。
# 摘錄的寫入時間與模型一起取：Claude 批次裡由 DeepSeek 後補的摘錄不算 Claude 產出（模組 docstring）。
REPORT_SQL = """
SELECT r.id::text AS report_id, r.file_hash, r.created_at, r.title, r.summary,
       EXISTS (SELECT 1 FROM research.report_takeaway t WHERE t.report_id = r.id) AS has_takeaway,
       (SELECT max(t.created_at) FROM research.report_takeaway t WHERE t.report_id = r.id) AS takeaway_at,
       (SELECT max(t.raw_payload->>'model') FROM research.report_takeaway t WHERE t.report_id = r.id)
           AS takeaway_model
FROM research.research_report r
WHERE r.full_text IS NOT NULL AND r.is_research IS NOT FALSE
  AND ((r.created_at >= :before_start AND r.created_at < :until)
       OR r.file_hash = ANY(CAST(:usage_hashes AS text[])))
""".strip()

# 入庫的取報告 created_at（回填會刷新 extraction_log.updated_at）；非研報沒有報告列，取 updated_at。
TAG_SQL = """
SELECT l.file_hash, l.stopped_at, COALESCE(r.created_at, l.updated_at) AS at, r.market, r.is_research
FROM research.extraction_log l
LEFT JOIN research.research_report r ON r.file_hash = l.file_hash
WHERE l.stopped_at IN ('ingested', 'not_research')
  AND COALESCE(r.created_at, l.updated_at) >= :before_start
  AND COALESCE(r.created_at, l.updated_at) < :until
""".strip()

SKIP_LIST_SQL = """
SELECT file_hash, task, reason, model, fail_count
FROM research.llm_task_failure
WHERE first_at >= :switch_at
""".strip()


# ── 窗期與分群 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Window:
    switch_at: datetime
    before_start: datetime
    until: datetime
    grace: timedelta = timedelta(hours=6)
    claude_until: Optional[datetime] = None  # CLI 失效時點；到 switch_at 之間是事故空窗，兩群都不收

    @property
    def claude_end(self) -> datetime:
        """Claude 群的右端：`claude_until` 與 `switch_at` 取早的。"""
        if self.claude_until is None:
            return self.switch_at
        return min(self.claude_until, self.switch_at)

    @property
    def gap(self) -> Optional[tuple[datetime, datetime]]:
        """事故空窗 `[claude_end, switch_at)`；沒有空窗回 None。"""
        return (self.claude_end, self.switch_at) if self.claude_end < self.switch_at else None

    def cohort(self, ts: Optional[datetime]) -> Optional[str]:
        """入庫批次（缺值率用）：Claude 批次不含事故空窗；DeepSeek 批次扣掉最近 grace 小時（下游批次還沒輪到）。"""
        if ts is None:
            return None
        if self.before_start <= ts < self.claude_end:
            return CLAUDE
        if self.switch_at <= ts < self.until - self.grace:
            return DEEPSEEK
        return None


@dataclass(frozen=True)
class UsageHit:
    ts: datetime
    model: str


def model_group(model: Optional[str]) -> str:
    """模型名稱 → 群。白名單或 deepseek- 開頭是 DeepSeek；其餘（含缺值）是 Claude。"""
    if model and (is_http_model(model) or model.startswith("deepseek")):
        return DEEPSEEK
    return CLAUDE


def attribute(*, model_key: Optional[str], ts: Optional[datetime], window: Window,
              usage: Optional[UsageHit] = None) -> Optional[str]:
    """一筆產出屬於哪一群；不在窗期內回 None。

    依序：明確的 `raw_payload.model` → 切換後的成功用量紀錄 → 時間（Claude 窗期內 Claude、切換後 DeepSeek，
    依據 D-C：CLI 已永久失效，切換後的寫入只可能來自 DeepSeek）。事故空窗 `[claude_end, switch_at)` 的
    時間一律回 None（兩群都不收）。
    """
    if ts is not None and ts >= window.until:
        return None
    if model_key:
        if model_group(model_key) == DEEPSEEK:
            return DEEPSEEK
        return CLAUDE if ts is not None and window.before_start <= ts < window.claude_end else None
    if usage is not None and usage.ts >= window.switch_at:
        return model_group(usage.model)
    if ts is None or ts < window.before_start or ts >= window.until:
        return None
    if ts < window.claude_end:
        return CLAUDE
    return DEEPSEEK if ts >= window.switch_at else None


def _aware(ts) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def load_usage(path: Optional[Path], tasks: Iterable[str]) -> tuple[dict[tuple[str, str], UsageHit], dict]:
    """讀用量紀錄：每個 (task, file_hash) 留最後一次成功呼叫。檔案不存在回空索引（只讀、壞行略過）。"""
    wanted = set(tasks)
    index: dict[tuple[str, str], UsageHit] = {}
    stats = {"path": str(path) if path else None, "exists": False, "lines": 0, "used": 0, "bad_lines": 0}
    if path is None or not path.exists():
        return index, stats
    stats["exists"] = True
    with path.open(encoding="utf-8") as f:
        for line in f:
            stats["lines"] += 1
            try:
                row = json.loads(line)
                task, fh, kind = row.get("task"), row.get("file_hash"), row.get("kind")
                model = row.get("model_resp") or row.get("model_req")
                ts = _aware(row.get("ts"))
            except (ValueError, AttributeError, TypeError):
                stats["bad_lines"] += 1
                continue
            if task not in wanted or not fh or kind is not None or not model or ts is None:
                continue
            stats["used"] += 1
            prev = index.get((task, fh))
            if prev is None or ts >= prev.ts:
                index[(task, fh)] = UsageHit(ts, model)
    return index, stats


def usage_hashes(index: dict[tuple[str, str], UsageHit], tasks: Iterable[str], since: datetime) -> list[str]:
    wanted = set(tasks)
    return sorted({fh for (task, fh), hit in index.items() if task in wanted and hit.ts >= since})


# ── 統計 ─────────────────────────────────────────────────────────────────────


def wilson(k: int, n: int, z: float = Z95) -> Optional[tuple[float, float]]:
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def newcombe(k1: int, n1: int, k0: int, n0: int, z: float = Z95) -> Optional[tuple[float, float, float]]:
    """p1 − p0 的點估計與 Newcombe（Wilson 混合）區間。1＝DeepSeek、0＝Claude。"""
    w1, w0 = wilson(k1, n1, z), wilson(k0, n0, z)
    if w1 is None or w0 is None:
        return None
    p1, p0 = k1 / n1, k0 / n0
    d = p1 - p0
    lo = d - math.sqrt((p1 - w1[0]) ** 2 + (w0[1] - p0) ** 2)
    hi = d + math.sqrt((w1[1] - p1) ** 2 + (p0 - w0[0]) ** 2)
    return d, lo, hi


def _percentile(sorted_vals: Sequence[float], q: float) -> float:
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _ratio(pairs: Sequence[tuple[int, int]]) -> Optional[float]:
    n = sum(b for _, b in pairs)
    return sum(a for a, _ in pairs) / n if n else None


def cluster_bootstrap_diff(ds: Sequence[tuple[int, int]], cl: Sequence[tuple[int, int]], *,
                           n_boot: int, seed: int) -> Optional[tuple[float, float, float]]:
    """(命中, 分母) 以研報為單位重抽，回 (差值點估計, 2.5%, 97.5%)。任一邊沒有分母回 None。"""
    point_ds, point_cl = _ratio(ds), _ratio(cl)
    if point_ds is None or point_cl is None:
        return None
    rng = random.Random(seed)
    diffs = []
    for _ in range(n_boot):
        a = _ratio([ds[rng.randrange(len(ds))] for _ in ds])
        b = _ratio([cl[rng.randrange(len(cl))] for _ in cl])
        if a is not None and b is not None:
            diffs.append(a - b)
    if not diffs:
        return None
    diffs.sort()
    return point_ds - point_cl, _percentile(diffs, 0.025), _percentile(diffs, 0.975)


def cluster_bootstrap_ci(pairs: Sequence[tuple[int, int]], *, n_boot: int, seed: int) -> Optional[tuple[float, float]]:
    if _ratio(pairs) is None:
        return None
    rng = random.Random(seed)
    vals = sorted(v for v in (_ratio([pairs[rng.randrange(len(pairs))] for _ in pairs]) for _ in range(n_boot))
                  if v is not None)
    return (_percentile(vals, 0.025), _percentile(vals, 0.975)) if vals else None


def mean_diff_bootstrap(ds: Sequence[float], cl: Sequence[float], *, n_boot: int,
                        seed: int) -> Optional[tuple[float, float, float]]:
    if not ds or not cl:
        return None
    rng = random.Random(seed)
    diffs = sorted(
        sum(ds[rng.randrange(len(ds))] for _ in ds) / len(ds) - sum(cl[rng.randrange(len(cl))] for _ in cl) / len(cl)
        for _ in range(n_boot)
    )
    return sum(ds) / len(ds) - sum(cl) / len(cl), _percentile(diffs, 0.025), _percentile(diffs, 0.975)


def judge(diff: Optional[tuple[float, float, float]], *, direction: str, margin: float) -> dict:
    """依 §判準的方向取端點：越高越好看下界、越低越好看上界。只判讀、不做任何動作。"""
    end = "差值 CI 下界" if direction == HIGHER else "差值 CI 上界"
    bound = -margin if direction == HIGHER else margin
    out = {"direction": direction, "margin": margin, "bound": bound, "end": end}
    if diff is None:
        return {**out, "verdict": VERDICT_NO_DATA, "end_value": None, "point_within": None}
    d, lo, hi = diff
    if direction == HIGHER:
        verdict = VERDICT_PASS if lo >= bound else (VERDICT_FAIL if hi < bound else VERDICT_UNSURE)
        return {**out, "verdict": verdict, "end_value": lo, "point_within": d >= bound}
    verdict = VERDICT_PASS if hi <= bound else (VERDICT_FAIL if lo > bound else VERDICT_UNSURE)
    return {**out, "verdict": verdict, "end_value": hi, "point_within": d <= bound}


def prop(k: int, n: int) -> dict:
    ci = wilson(k, n)
    return {"k": k, "n": n, "rate": (k / n) if n else None, "ci": list(ci) if ci else None}


def _diff_dict(diff: Optional[tuple[float, float, float]]) -> Optional[dict]:
    return None if diff is None else {"point": diff[0], "ci": [diff[1], diff[2]]}


def _lengths(vals: Sequence[int]) -> Optional[dict]:
    if not vals:
        return None
    s = sorted(vals)
    return {"n": len(s), "p10": _percentile(s, 0.1), "p50": _percentile(s, 0.5), "p90": _percentile(s, 0.9),
            "mean": sum(s) / len(s)}


# ── 分析（純函式，測試直接餵假列）──────────────────────────────────────────────


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def analyze_takeaways(rows: Sequence[dict], texts: dict[str, Optional[str]], reports: Sequence[dict],
                      window: Window, *, n_boot: int, seed: int) -> dict:
    per_report: dict[str, dict] = {}
    for r in rows:
        g = attribute(model_key=r.get("model"), ts=_aware(r.get("created_at")), window=window)
        if g is None:
            continue
        rep = per_report.setdefault(r["report_id"], {"groups": set(), "rows": []})
        rep["groups"].add(g)
        rep["rows"].append(r)

    groups = {g: {"reports": 0, "items": [], "pairs_any": [], "pairs_exact": [], "sha_mismatch_reports": 0,
                  "backfilled_reports": 0, "stored_status": Counter()} for g in GROUPS}
    mixed = 0
    for rid, rep in per_report.items():
        if len(rep["groups"]) != 1:
            mixed += 1  # 同一篇的列分屬兩群（不該發生：摘錄整篇重寫），不計
            continue
        g = next(iter(rep["groups"]))
        agg = groups[g]
        agg["reports"] += 1
        agg["items"].append(len(rep["rows"]))
        for r in rep["rows"]:
            agg["stored_status"][r.get("extraction_status") or "?"] += 1
        if any(_backfilled_after(r) for r in rep["rows"]):
            # 擷取後被回填過：reanchor_takeaways 已把 sha 換成新文字的，只看 sha 擋不到（模組 docstring）
            agg["backfilled_reports"] += 1
            continue
        canonical = clean_extracted(texts.get(rid) or "")
        if any(r.get("text_sha256") != _sha256(canonical) for r in rep["rows"]):
            agg["sha_mismatch_reports"] += 1
            continue
        quotes = [r["quote"] for r in rep["rows"] if r.get("quote")]
        if not quotes:
            continue
        anchors = [locate_quote(canonical, q) for q in quotes]
        agg["pairs_any"].append((sum(1 for a in anchors if a is not None), len(quotes)))
        agg["pairs_exact"].append((sum(1 for a in anchors if a is not None and a.method == "exact"), len(quotes)))

    out: dict = {"mixed_reports": mixed, "groups": {}}
    for g, agg in groups.items():
        k_any = sum(a for a, _ in agg["pairs_any"])
        k_exact = sum(a for a, _ in agg["pairs_exact"])
        n_q = sum(b for _, b in agg["pairs_any"])
        out["groups"][g] = {
            "reports": agg["reports"],
            "backfilled_reports": agg["backfilled_reports"],
            "sha_mismatch_reports": agg["sha_mismatch_reports"],
            "items_per_report": (sum(agg["items"]) / len(agg["items"])) if agg["items"] else None,
            "quotes": n_q,
            "anchor_any": {"k": k_any, "n": n_q, "rate": (k_any / n_q) if n_q else None,
                           "ci": _list(cluster_bootstrap_ci(agg["pairs_any"], n_boot=n_boot, seed=seed))},
            "anchor_exact": {"k": k_exact, "n": n_q, "rate": (k_exact / n_q) if n_q else None,
                             "ci": _list(cluster_bootstrap_ci(agg["pairs_exact"], n_boot=n_boot, seed=seed))},
            "stored_status": dict(agg["stored_status"]),
        }
    any_diff = cluster_bootstrap_diff(groups[DEEPSEEK]["pairs_any"], groups[CLAUDE]["pairs_any"],
                                      n_boot=n_boot, seed=seed)
    exact_diff = cluster_bootstrap_diff(groups[DEEPSEEK]["pairs_exact"], groups[CLAUDE]["pairs_exact"],
                                        n_boot=n_boot, seed=seed)
    out["anchor_any_diff"] = _diff_dict(any_diff)
    out["anchor_any_judgement"] = judge(any_diff, direction=HIGHER, margin=TOL_ANCHOR)
    out["anchor_exact_diff"] = _diff_dict(exact_diff)
    out["items_diff"] = _diff_dict(mean_diff_bootstrap(
        [float(x) for x in groups[DEEPSEEK]["items"]], [float(x) for x in groups[CLAUDE]["items"]],
        n_boot=n_boot, seed=seed))

    # 產出率（1 − 未產出率）：以入庫批次為母體。rejected 不寫列，所以「沒有任何摘錄列」＝rejected＋失敗＋未跑。
    # 已產出只算該批次自己的模型寫的：Claude 批次裡由 DeepSeek 後補（或事故空窗寫入）的摘錄算未產出，另列後補。
    produced = {g: [0, 0] for g in GROUPS}
    late = {g: 0 for g in GROUPS}
    for rep in reports:
        c = window.cohort(_aware(rep.get("created_at")))
        if c is None:
            continue
        produced[c][1] += 1
        if not rep.get("has_takeaway"):
            continue
        g = attribute(model_key=rep.get("takeaway_model"), ts=_aware(rep.get("takeaway_at")), window=window)
        if g == c:
            produced[c][0] += 1
        else:
            late[c] += 1
    out["produced"] = {g: {**prop(*produced[g]), "late_fill": late[g]} for g in GROUPS}
    pdiff = newcombe(produced[DEEPSEEK][0], produced[DEEPSEEK][1], produced[CLAUDE][0], produced[CLAUDE][1])
    out["produced_diff"] = _diff_dict(pdiff)
    out["produced_judgement"] = judge(pdiff, direction=HIGHER, margin=TOL_PARSE)
    return out


def _backfilled_after(row: dict) -> bool:
    """extraction_log.updated_at 晚於這筆摘錄的寫入時間＝擷取後全文被回填（或重新入庫）改過。"""
    log_at, created = _aware(row.get("log_updated_at")), _aware(row.get("created_at"))
    return log_at is not None and created is not None and log_at > created


def _list(t: Optional[tuple]) -> Optional[list]:
    return list(t) if t is not None else None


def _thesis(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def analyze_signals(rows: Sequence[dict], window: Window, usage: dict[tuple[str, str], UsageHit]) -> dict:
    status = {g: Counter() for g in GROUPS}
    stance = {g: {d: Counter() for d in THESIS_DIMENSIONS} for g in GROUPS}
    for r in rows:
        g = attribute(model_key=r.get("model"), ts=_aware(r.get("created_at")), window=window,
                      usage=usage.get((TASK_SIGNAL, r.get("file_hash"))))
        if g is None:
            continue
        st = r.get("extraction_status")
        status[g][st] += 1
        if st in ("valid", "partial"):
            thesis = _thesis(r.get("thesis_dimensions"))
            for d in THESIS_DIMENSIONS:
                item = thesis.get(d)
                value = item.get("stance") if isinstance(item, dict) else None
                stance[g][d][value if value in STANCE_CONSTRUCTIVENESS[d] else "（缺或不在詞彙內）"] += 1
    out: dict = {"groups": {}}
    for g in GROUPS:
        c = status[g]
        n = c["valid"] + c["partial"] + c["rejected"]
        out["groups"][g] = {
            "rows": n,
            "valid": prop(c["valid"], n),
            "partial": prop(c["partial"], n),
            "rejected": prop(c["rejected"], n),
            "ok": prop(c["valid"] + c["partial"], n),
            "stance": {d: dict(stance[g][d]) for d in THESIS_DIMENSIONS},
        }
    ds, cl = out["groups"][DEEPSEEK]["ok"], out["groups"][CLAUDE]["ok"]
    diff = newcombe(ds["k"], ds["n"], cl["k"], cl["n"])
    out["ok_diff"] = _diff_dict(diff)
    out["ok_judgement"] = judge(diff, direction=HIGHER, margin=TOL_PARSE)
    return out


def analyze_text_field(reports: Sequence[dict], window: Window, usage: dict[tuple[str, str], UsageHit], *,
                       field_name: str, task: str) -> dict:
    """標題／摘要：入庫批次的填補率（應補未補）＋依產出模型分群的殘留簡體率與長度。"""
    filled = {g: [0, 0] for g in GROUPS}
    late = {g: 0 for g in GROUPS}
    simp = {g: [0, 0] for g in GROUPS}
    lengths: dict[str, list[int]] = {g: [] for g in GROUPS}
    for rep in reports:
        value = rep.get(field_name)
        created = _aware(rep.get("created_at"))
        c = window.cohort(created)
        g = attribute(model_key=None, ts=created, window=window,
                      usage=usage.get((task, rep.get("file_hash")))) if value else None
        if c is not None:
            filled[c][1] += 1
            # 已填只算該批次自己的模型產出的：Claude 批次由 DeepSeek 後補的值算未填、另列後補（M2）
            if value and g == c:
                filled[c][0] += 1
            elif value:
                late[c] += 1
        if g is None:
            continue
        simp[g][1] += 1
        simp[g][0] += 1 if count_simplified(value) > 0 else 0
        lengths[g].append(len(value))
    out: dict = {"groups": {}}
    for g in GROUPS:
        out["groups"][g] = {
            "filled": prop(*filled[g]),
            "missing": prop(filled[g][1] - filled[g][0], filled[g][1]),
            "late_fill": late[g],
            "simplified": prop(*simp[g]),
            "length": _lengths(lengths[g]),
        }
    fdiff = newcombe(filled[DEEPSEEK][0], filled[DEEPSEEK][1], filled[CLAUDE][0], filled[CLAUDE][1])
    out["filled_diff"] = _diff_dict(fdiff)
    out["filled_judgement"] = judge(fdiff, direction=HIGHER, margin=TOL_PARSE)
    out["simplified_diff"] = _diff_dict(newcombe(simp[DEEPSEEK][0], simp[DEEPSEEK][1],
                                                 simp[CLAUDE][0], simp[CLAUDE][1]))
    return out


def analyze_tags(rows: Sequence[dict], window: Window, usage: dict[tuple[str, str], UsageHit],
                 tag_lookup: Callable[[str], Optional[tuple[Optional[str], bool]]]) -> dict:
    """標註：skip_non_research（extraction_log 的 not_research）與 market=None 比例、is_research／market 分布。

    extraction_log 的 not_research 同時涵蓋「非研報」與「是研報但沒有市場」，要拆開得讀 tags 快取；
    讀不到的列只進 skip_non_research，不進 market／is_research 的分母。
    """
    skip = {g: [0, 0] for g in GROUPS}
    none_market = {g: [0, 0] for g in GROUPS}
    is_research = {g: Counter() for g in GROUPS}
    markets = {g: Counter() for g in GROUPS}
    unknown_tag = Counter()
    for r in rows:
        g = attribute(model_key=None, ts=_aware(r.get("at")), window=window,
                      usage=usage.get((TASK_TAG, r.get("file_hash"))))
        if g is None:
            continue
        not_research = r.get("stopped_at") == "not_research"
        skip[g][1] += 1
        skip[g][0] += 1 if not_research else 0
        if not_research:
            info = tag_lookup(r["file_hash"])
        else:
            info = (r.get("market"), bool(r.get("is_research")) if r.get("is_research") is not None else True)
        if info is None:
            unknown_tag[g] += 1
            continue
        market, research = info
        none_market[g][1] += 1
        none_market[g][0] += 1 if market is None else 0
        is_research[g][str(bool(research)).lower()] += 1
        markets[g][market or "None"] += 1
    out: dict = {"groups": {}}
    for g in GROUPS:
        out["groups"][g] = {
            "skip_non_research": prop(*skip[g]),
            "market_none": prop(*none_market[g]),
            "tag_unknown": unknown_tag[g],
            "is_research": dict(is_research[g]),
            "market": dict(markets[g].most_common()),
        }
    sdiff = newcombe(skip[DEEPSEEK][0], skip[DEEPSEEK][1], skip[CLAUDE][0], skip[CLAUDE][1])
    mdiff = newcombe(none_market[DEEPSEEK][0], none_market[DEEPSEEK][1], none_market[CLAUDE][0],
                     none_market[CLAUDE][1])
    out["skip_diff"] = _diff_dict(sdiff)
    out["skip_judgement"] = judge(sdiff, direction=LOWER, margin=0.0)
    out["market_none_diff"] = _diff_dict(mdiff)
    out["market_none_judgement"] = judge(mdiff, direction=LOWER, margin=0.0)
    return out


def analyze_skip_list(rows: Optional[Sequence[dict]]) -> dict:
    if rows is None:
        return {"table_ready": False, "by_task_reason": [], "total": 0}
    counts: Counter = Counter()
    skipping: Counter = Counter()
    for r in rows:
        key = (r["task"], r["reason"])
        counts[key] += 1
        rec = FailureRecord(reason=r["reason"], model=r["model"], fail_count=int(r["fail_count"]))
        skipping[key] += 1 if should_skip(rec, r["model"]) else 0
    items = [{"task": t, "reason": reason, "count": n, "skipping": skipping[(t, reason)]}
             for (t, reason), n in sorted(counts.items())]
    return {"table_ready": True, "by_task_reason": items, "total": sum(counts.values())}


# ── 用量紀錄判準（批次 A：content_filter、截斷、401／402）與斷路器標記 ──────────────────


def read_usage_rows(path: Optional[Path], window: Window) -> tuple[list[dict], dict]:
    """切換後（`[switch_at, until)`）`backend="http"` 的用量紀錄列，全部 kind 都留（只讀、壞行略過）。"""
    rows: list[dict] = []
    stats = {"path": str(path) if path else None, "exists": False, "lines": 0, "bad_lines": 0, "rows": 0}
    if path is None or not path.exists():
        return rows, stats
    stats["exists"] = True
    with path.open(encoding="utf-8") as f:
        for line in f:
            stats["lines"] += 1
            try:
                row = json.loads(line)
                ts = _aware(row.get("ts"))
                backend = row.get("backend")
            except (ValueError, AttributeError, TypeError):
                stats["bad_lines"] += 1
                continue
            if backend != "http" or ts is None or not (window.switch_at <= ts < window.until):
                continue
            rows.append({**row, "ts": ts})
    stats["rows"] = len(rows)
    return rows, stats


def judge_rate_cap(k: int, n: int, cap: float) -> dict:
    """比例 ≤ cap：看 Wilson CI 上界（§判準 content_filter）。"""
    ci = wilson(k, n)
    out = {"cap": cap, "end": "比例 CI 上界"}
    if ci is None:
        return {**out, "verdict": VERDICT_NO_DATA, "end_value": None}
    verdict = VERDICT_PASS if ci[1] <= cap else (VERDICT_FAIL if ci[0] > cap else VERDICT_UNSURE)
    return {**out, "verdict": verdict, "end_value": ci[1]}


def judge_zero(count: Optional[int]) -> str:
    """計數判準（截斷、401、402）：0 才通過；沒有資料＝樣本不足。"""
    if count is None:
        return VERDICT_NO_DATA
    return VERDICT_PASS if count == 0 else VERDICT_FAIL


def analyze_usage_health(rows: Sequence[dict], stats: dict, skip_rows: Optional[Sequence[dict]]) -> dict:
    """零 LLM：由用量紀錄算 §判準／批次 A 的 content_filter、截斷、401／402，外加每日 token（觀測）。"""
    available = bool(stats.get("exists"))
    per_task: dict[str, Counter] = {}
    cf_hashes: dict[str, set] = {}
    truncations: list[dict] = []
    last_success: dict[tuple[str, str], datetime] = {}
    daily: dict[str, Counter] = {}
    for r in rows:
        task = r.get("task") or "-"
        kind = r.get("kind")
        c = per_task.setdefault(task, Counter())
        c["calls"] += 1
        c[kind or "ok"] += 1
        if kind not in NO_CONTENT_VERDICT_KINDS:
            c["judged"] += 1
        fh = r.get("file_hash")
        if kind == KIND_CONTENT_FILTER and fh:
            cf_hashes.setdefault(task, set()).add(fh)
        if kind == KIND_TRUNCATED:
            truncations.append({"task": task, "file_hash": fh, "ts": r["ts"]})
        if kind is None and fh:
            key = (task, fh)
            if key not in last_success or r["ts"] > last_success[key]:
                last_success[key] = r["ts"]
        day = daily.setdefault(r["ts"].astimezone(TAIPEI).date().isoformat(), Counter())
        day["calls"] += 1
        tokens = r.get("tokens") if isinstance(r.get("tokens"), dict) else {}
        for k in ("hit", "miss", "completion", "reasoning"):
            day[k] += int(tokens.get(k) or 0)

    tasks = {}
    for task in sorted(per_task):
        c = per_task[task]
        tasks[task] = {
            "calls": c["calls"],
            "content_filter": {**prop(c[KIND_CONTENT_FILTER], c["judged"]),
                               "reports": len(cf_hashes.get(task, ())),
                               "judgement": judge_rate_cap(c[KIND_CONTENT_FILTER], c["judged"], CONTENT_FILTER_CAP)},
            "truncated": c[KIND_TRUNCATED],
            "timeout_streamed": c[KIND_TIMEOUT_STREAMED],
            "kinds": {k: v for k, v in sorted(c.items()) if k not in ("calls", "judged")},
        }

    # 截斷的都記入跳過表了嗎（批次 A）：成功會刪跳過名單那列，所以之後成功過的也算處理掉了
    skip_keys = None if skip_rows is None else {(s.get("task"), s.get("file_hash")) for s in skip_rows}
    trunc = {"recorded": 0, "later_success": 0, "unrecorded": [], "no_file_hash": 0, "unknown": 0}
    for t in truncations:
        key = (t["task"], t["file_hash"])
        if not t["file_hash"]:
            trunc["no_file_hash"] += 1  # 簡報：不進跳過名單（generate_brief 自己處置）
        elif skip_keys is None:
            trunc["unknown"] += 1
        elif key in skip_keys:
            trunc["recorded"] += 1
        elif key in last_success and last_success[key] > t["ts"]:
            trunc["later_success"] += 1
        else:
            trunc["unrecorded"].append({"task": t["task"], "file_hash": t["file_hash"]})

    def total(kind):
        return sum(per_task[t][kind] for t in per_task) if available else None

    return {
        "available": available,
        "tasks": tasks,
        "truncated": total(KIND_TRUNCATED),
        "timeout_streamed": total(KIND_TIMEOUT_STREAMED),
        "auth_401": total(KIND_AUTH),
        "quota_402": total(KIND_QUOTA),
        "config": total("config"),
        "truncation_skip_check": trunc,
        "daily_tokens": {d: dict(daily[d]) for d in sorted(daily)},
    }


def read_breaker(path: Optional[Path]) -> dict:
    """斷路器標記（`key=value` 行；只讀）。每次跳脫覆寫、不會自己刪：只看得到最後一次。"""
    out = {"path": str(path) if path else None, "exists": False, "ts": None, "round": None, "reason": None}
    if path is None or not path.exists():
        return out
    out["exists"] = True
    try:
        body = path.read_text(encoding="utf-8")
    except OSError as exc:
        out["reason"] = f"讀不到：{type(exc).__name__}"
        return out
    for line in body.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        if key == "ts":
            try:
                out["ts"] = _aware(value.strip())
            except ValueError:
                out["ts"] = None
        elif key in ("round", "reason"):
            out[key] = value.strip()
    return out


def judge_breaker(marker: dict, window: Window) -> str:
    """標記的 ts 在切換後＝觸發過（劣化）；沒有標記或早於切換＝通過；有標記但讀不出時間＝未定。"""
    if not marker.get("exists"):
        return VERDICT_PASS
    ts = marker.get("ts")
    if ts is None:
        return "未定（標記讀不出時間）"
    return VERDICT_FAIL if window.switch_at <= ts else VERDICT_PASS


# ── 取數（唯讀）─────────────────────────────────────────────────────────────────


@dataclass
class RawData:
    takeaways: list[dict] = field(default_factory=list)
    texts: dict[str, Optional[str]] = field(default_factory=dict)
    signals: list[dict] = field(default_factory=list)
    reports: list[dict] = field(default_factory=list)
    tags: list[dict] = field(default_factory=list)
    skip_list: Optional[list[dict]] = None


def query_plan(window: Window, usage: dict[tuple[str, str], UsageHit]) -> list[tuple[str, str, dict]]:
    """(名稱, SQL, 參數)；--dry-run 印它，fetch 照它跑（全文那句的 ids 要等摘錄列取回才知道）。"""
    base = {"before_start": window.before_start, "until": window.until}
    return [
        ("read_only", READ_ONLY_SQL, {}),
        ("takeaways", TAKEAWAY_SQL, base),
        ("full_text", FULL_TEXT_SQL, {"ids": "<摘錄列涉及的 report_id>"}),
        ("signals", SIGNAL_SQL, {"before_start": window.before_start,
                                 "usage_hashes": usage_hashes(usage, [TASK_SIGNAL], window.switch_at)}),
        ("reports", REPORT_SQL, {**base, "usage_hashes": usage_hashes(usage, [TASK_TITLE, TASK_SUMMARY],
                                                                     window.switch_at)}),
        ("tags", TAG_SQL, base),
        ("failure_table_ready", FAILURE_TABLE_READY_SQL, {}),
        ("skip_list", SKIP_LIST_SQL, {"switch_at": window.switch_at}),
    ]


async def fetch(window: Window, usage: dict[tuple[str, str], UsageHit], *, session_factory=None) -> RawData:
    sf = session_factory or SessionFactory
    plan = {name: (sql, params) for name, sql, params in query_plan(window, usage)}
    data = RawData()

    async def rows(name: str, **override) -> list[dict]:
        sql, params = plan[name]
        result = await session.execute(text(sql), {**params, **override})
        return [dict(r) for r in result.mappings().all()]

    async with sf() as session:
        # 交易的第一句必須是 SET TRANSACTION；之後的 SET LOCAL 只活到交易結束。
        await session.execute(text(READ_ONLY_SQL))
        await relax_statement_timeout(session)
        data.takeaways = await rows("takeaways")
        ids = sorted({r["report_id"] for r in data.takeaways})
        if ids:
            data.texts = {r["report_id"]: r["full_text"] for r in await rows("full_text", ids=ids)}
        data.signals = await rows("signals")
        data.reports = await rows("reports")
        data.tags = await rows("tags")
        ready = (await session.execute(text(FAILURE_TABLE_READY_SQL))).scalar()
        if ready:
            data.skip_list = await rows("skip_list")
        await session.rollback()
    return data


def tags_lookup_from(tags_dir: Path) -> Callable[[str], Optional[tuple[Optional[str], bool]]]:
    def lookup(file_hash: str):
        try:
            tag = load_tag(tags_dir, file_hash)
        except OSError:
            return None
        return None if tag is None else (tag.market, bool(tag.is_research))
    return lookup


# ── 彙整與輸出 ──────────────────────────────────────────────────────────────────


# 判讀總表與用量紀錄判準都量不到的批次 A／B 觀測條件（計畫第四版）：報告逐條列出，要另外看。
UNCOVERED = (
    "幻覺率（§判準：差值 CI 上界 ≤ 0）：零 LLM 量不到，需人工抽查摘錄／摘要／訊號對原文。",
    "每日花費金額（批次 A：≤ 估算的 1.5 倍）：用量紀錄只有 token（「用量紀錄判準」一節的每日 token 只是觀測），"
    "金額看 DeepSeek 後台或 `/healthz/llm`（只回答本機直連）的餘額差分。",
    "content_filter「不集中在特定題材」：這裡只列篇數，題材是否集中逐筆看 `make llm-blocked`。",
    "標註 is_research 翻轉（批次 B）：同一篇沒有兩個模型的標註，要逐筆人工看。",
    "標註 `skip_blocked` 是否都已處理（批次 B）：看 `make llm-blocked` 與 sync 保留檔。",
    "斷路器：標記檔只留最後一次跳脫；歷次跳脫看 `data/unit_failures.log` 的段 rc=2 與 journal。",
    "問答（受控重播閘門、`/api/ask` 的 401／402）：不寫批次用量紀錄，不在本腳本範圍。",
)


def build_report(data: RawData, window: Window, usage: dict[tuple[str, str], UsageHit], usage_stats: dict, *,
                 tag_lookup, n_boot: int = 2000, seed: int = 20260925,
                 health_rows: Sequence[dict] = (), health_stats: Optional[dict] = None,
                 breaker: Optional[dict] = None) -> dict:
    tk = analyze_takeaways(data.takeaways, data.texts, data.reports, window, n_boot=n_boot, seed=seed)
    sig = analyze_signals(data.signals, window, usage)
    title = analyze_text_field(data.reports, window, usage, field_name="title", task=TASK_TITLE)
    summary = analyze_text_field(data.reports, window, usage, field_name="summary", task=TASK_SUMMARY)
    tag = analyze_tags(data.tags, window, usage, tag_lookup)
    skip = analyze_skip_list(data.skip_list)
    health = analyze_usage_health(health_rows, health_stats or {"exists": False}, data.skip_list)
    breaker = breaker or read_breaker(None)

    def row(metric, criterion, groups_pair, diff, judgement):
        return {"metric": metric, "criterion": criterion, "claude": groups_pair[0], "deepseek": groups_pair[1],
                "diff": diff, **{k: judgement[k] for k in ("end", "end_value", "verdict", "point_within")}}

    def rates(block, key):
        return (block["groups"][CLAUDE][key]["rate"], block["groups"][DEEPSEEK][key]["rate"])

    verdicts = [
        row("摘錄：任一方式錨定成功率（主，D-A）", "差值 ≥ −5pp", rates(tk, "anchor_any"),
            tk["anchor_any_diff"], tk["anchor_any_judgement"]),
        row("摘錄：產出率（1 − 未產出率，D-N）", "差值 ≥ −2pp",
            (tk["produced"][CLAUDE]["rate"], tk["produced"][DEEPSEEK]["rate"]),
            tk["produced_diff"], tk["produced_judgement"]),
        row("訊號：非 rejected 率（D-N）", "差值 ≥ −2pp", rates(sig, "ok"), sig["ok_diff"], sig["ok_judgement"]),
        row("標題：填補率（D-N）", "差值 ≥ −2pp", rates(title, "filled"), title["filled_diff"],
            title["filled_judgement"]),
        row("摘要：填補率（D-N）", "差值 ≥ −2pp", rates(summary, "filled"), summary["filled_diff"],
            summary["filled_judgement"]),
        row("標註：skip_non_research 比例（批次 B）", "差值 ≤ 0", rates(tag, "skip_non_research"),
            tag["skip_diff"], tag["skip_judgement"]),
        row("標註：market=None 比例（批次 B）", "差值 ≤ 0", rates(tag, "market_none"),
            tag["market_none_diff"], tag["market_none_judgement"]),
    ]
    ht = health["tasks"]
    usage_verdicts = [
        {"metric": f"content_filter 比例：{task}", "criterion": "≤ 1%（比例 CI 上界）",
         "value": ht[task]["content_filter"], "verdict": ht[task]["content_filter"]["judgement"]["verdict"]}
        for task in ht
    ] + [
        {"metric": "截斷（truncated，finish_reason=length）", "criterion": "0", "value": health["truncated"],
         "verdict": judge_zero(health["truncated"])},
        {"metric": "401（auth）", "criterion": "0", "value": health["auth_401"],
         "verdict": judge_zero(health["auth_401"])},
        {"metric": "402（quota）", "criterion": "0", "value": health["quota_402"],
         "verdict": judge_zero(health["quota_402"])},
        {"metric": "斷路器標記", "criterion": "切換後沒有觸發", "value": breaker,
         "verdict": judge_breaker(breaker, window)},
    ]
    if not ht:
        usage_verdicts.insert(0, {"metric": "content_filter 比例", "criterion": "≤ 1%（比例 CI 上界）",
                                  "value": None, "verdict": VERDICT_NO_DATA})

    limitations = []
    if window.gap:
        limitations.append(f"事故空窗 `[{window.gap[0].isoformat()}, {window.gap[1].isoformat()})`（claude CLI "
                           "失效到切換）兩群都不收：這段時間入庫的研報、寫入的產出不計（`--claude-until`）。")
    if not usage_stats.get("exists"):
        limitations.append(f"沒有用量紀錄（{usage_stats.get('path')}）：標題、摘要、標註、訊號只能依時間分群，"
                           "切換前入庫、切換後才補的產出會被算成 Claude（Claude 批次的標題／摘要填補率分不出"
                           "DeepSeek 後補，會偏高）；用量紀錄判準一節全部樣本不足。從 worktree 跑時用 "
                           "`--usage-log` 指到部署目錄的 `data/llm_usage.jsonl`。")
    if not breaker.get("exists"):
        limitations.append(f"斷路器標記 {breaker.get('path')} 不存在，判「通過」只在它指到部署目錄時才有意義"
                           "（從 worktree 跑時用 `--breaker-file`）。")
    backfilled = sum(tk["groups"][g]["backfilled_reports"] for g in GROUPS)
    if backfilled:
        limitations.append(f"{backfilled} 篇摘錄在擷取後被回填（extraction_log.updated_at 較晚），不計錨定率；"
                           "全庫回填期間不要跑這支。")
    if not skip["table_ready"]:
        limitations.append("research.llm_task_failure 不存在（沒跑 make schema？）：跳過名單一節為空。")
    if tk["mixed_reports"]:
        limitations.append(f"{tk['mixed_reports']} 篇摘錄的列分屬兩群，未計入。")
    return {
        "window": {"switch_at": window.switch_at.isoformat(), "before_start": window.before_start.isoformat(),
                   "claude_end": window.claude_end.isoformat(), "until": window.until.isoformat(),
                   "grace_hours": window.grace.total_seconds() / 3600},
        "sources": {"usage_log": usage_stats, "usage_health": health_stats or {"exists": False}},
        "verdicts": verdicts,
        "takeaway": tk,
        "signal": sig,
        "title": title,
        "summary": summary,
        "tag": tag,
        "skip_list": skip,
        "usage_health": health,
        "usage_verdicts": usage_verdicts,
        "breaker": breaker,
        "limitations": limitations,
        "uncovered": list(UNCOVERED),
        "bootstrap": {"n_boot": n_boot, "seed": seed},
    }


def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def _pp(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:+.1f}pp"


def _ci(ci: Optional[Sequence[float]], fmt=_pct) -> str:
    return "—" if not ci else f"[{fmt(ci[0])}, {fmt(ci[1])}]"


def _p(block: dict) -> str:
    return f"{_pct(block['rate'])}（{block['k']}/{block['n']}，CI {_ci(block['ci'])}）"


def _diff(d: Optional[dict]) -> str:
    return "—" if d is None else f"{_pp(d['point'])} {_ci(d['ci'], _pp)}"


def render_markdown(rep: dict) -> str:
    w = rep["window"]
    L = [
        "# DeepSeek 切換後批次產出觀測",
        "",
        "> **判讀總表全部通過不等於批次 A 觀測完成**：幻覺率、每日花費金額等本報告量不到，見文末「未涵蓋項目」。",
        "",
        f"- 切換時點 `{w['switch_at']}`；Claude 群 `[{w['before_start']}, {w['claude_end']})`；"
        f"DeepSeek 群 `[{w['switch_at']}, {w['until']})`（缺值率另扣最近 {w['grace_hours']:g} 小時）",
        f"- 用量紀錄：{'有' if rep['sources']['usage_log'].get('exists') else '無'}"
        f"（採用 {rep['sources']['usage_log'].get('used', 0)} 行）；CI 95%，bootstrap {rep['bootstrap']['n_boot']} 次",
        "- **只輸出判讀，不做任何切換。** 劣化時的處置只有修 prompt 或換 `deepseek-v4-pro`（沒有 Claude 可退）。",
        "",
        "## 判讀總表（計畫第四版 §判準）",
        "",
        "| 指標 | 判準 | Claude | DeepSeek | 差值（95% CI） | 取哪一端 | 端點值 | 點估計在容差內 | 判讀 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for v in rep["verdicts"]:
        within = "—" if v["point_within"] is None else ("是" if v["point_within"] else "否")
        L.append(f"| {v['metric']} | {v['criterion']} | {_pct(v['claude'])} | {_pct(v['deepseek'])} | "
                 f"{_diff(v['diff'])} | {v['end']} | {_pp(v['end_value'])} | {within} | **{v['verdict']}** |")

    tk = rep["takeaway"]
    L += ["", "## 摘錄", "", "| | Claude | DeepSeek |", "|---|---|---|"]
    gc, gd = tk["groups"][CLAUDE], tk["groups"][DEEPSEEK]
    L.append(f"| 研報數 | {gc['reports']} | {gd['reports']} |")
    L.append(f"| 有 quote 的條目 | {gc['quotes']} | {gd['quotes']} |")
    L.append(f"| 任一方式錨定成功率（主） | {_p(gc['anchor_any'])} | {_p(gd['anchor_any'])} |")
    L.append(f"| exact 錨定率（觀測） | {_p(gc['anchor_exact'])} | {_p(gd['anchor_exact'])} |")
    ipr = [("—" if g["items_per_report"] is None else f"{g['items_per_report']:.2f}") for g in (gc, gd)]
    L.append(f"| 每篇條數 | {ipr[0]} | {ipr[1]} |")
    L.append(f"| 未產出率（rejected＋失敗＋未跑＋他群後補；入庫批次） | {_p(_miss(tk['produced'][CLAUDE]))} | "
             f"{_p(_miss(tk['produced'][DEEPSEEK]))} |")
    L.append(f"| 其中由他群後補（算未產出） | {tk['produced'][CLAUDE].get('late_fill', 0)} | "
             f"{tk['produced'][DEEPSEEK].get('late_fill', 0)} |")
    L.append(f"| 擷取後被回填、不計錨定的研報 | {gc['backfilled_reports']} | {gd['backfilled_reports']} |")
    L.append(f"| 全文 sha 不符、不計錨定的研報 | {gc['sha_mismatch_reports']} | {gd['sha_mismatch_reports']} |")
    L.append(f"| 存的列狀態 | {gc['stored_status']} | {gd['stored_status']} |")
    L.append("")
    L.append(f"exact 錨定率差值 {_diff(tk['anchor_exact_diff'])}；每篇條數差值 "
             f"{_num_diff(tk['items_diff'])}（觀測，不判）。")

    sig = rep["signal"]
    L += ["", "## 訊號", "", "| | Claude | DeepSeek |", "|---|---|---|"]
    for key, label in (("valid", "valid"), ("partial", "partial"), ("rejected", "rejected")):
        L.append(f"| {label} | {_p(sig['groups'][CLAUDE][key])} | {_p(sig['groups'][DEEPSEEK][key])} |")
    L += ["", "stance 分布（valid／partial 列）：", ""]
    for d in THESIS_DIMENSIONS:
        L.append(f"- `{d}`：Claude {sig['groups'][CLAUDE]['stance'][d]}；"
                 f"DeepSeek {sig['groups'][DEEPSEEK]['stance'][d]}")

    for key, label in (("title", "標題"), ("summary", "摘要")):
        b = rep[key]
        L += ["", f"## {label}", "", "| | Claude | DeepSeek |", "|---|---|---|"]
        L.append(f"| 缺值率（應補未補＋他群後補；入庫批次） | {_p(b['groups'][CLAUDE]['missing'])} | "
                 f"{_p(b['groups'][DEEPSEEK]['missing'])} |")
        L.append(f"| 其中切換後由 DeepSeek 後補（算未填） | {b['groups'][CLAUDE]['late_fill']} | "
                 f"{b['groups'][DEEPSEEK]['late_fill']} |")
        L.append(f"| 殘留簡體率（觀測） | {_p(b['groups'][CLAUDE]['simplified'])} | "
                 f"{_p(b['groups'][DEEPSEEK]['simplified'])} |")
        L.append(f"| 長度 p10／p50／p90 | {_len(b['groups'][CLAUDE]['length'])} | "
                 f"{_len(b['groups'][DEEPSEEK]['length'])} |")

    tag = rep["tag"]
    L += ["", "## 標註", "", "| | Claude | DeepSeek |", "|---|---|---|"]
    L.append(f"| skip_non_research 比例 | {_p(tag['groups'][CLAUDE]['skip_non_research'])} | "
             f"{_p(tag['groups'][DEEPSEEK]['skip_non_research'])} |")
    L.append(f"| market=None 比例 | {_p(tag['groups'][CLAUDE]['market_none'])} | "
             f"{_p(tag['groups'][DEEPSEEK]['market_none'])} |")
    L.append(f"| 讀不到 tags 快取的非研報 | {tag['groups'][CLAUDE]['tag_unknown']} | "
             f"{tag['groups'][DEEPSEEK]['tag_unknown']} |")
    L.append(f"| is_research | {tag['groups'][CLAUDE]['is_research']} | {tag['groups'][DEEPSEEK]['is_research']} |")
    L.append(f"| market | {tag['groups'][CLAUDE]['market']} | {tag['groups'][DEEPSEEK]['market']} |")
    L += ["", "is_research 翻轉要逐筆人工看（同一篇沒有兩個模型的標註，這裡量不到）。",
          "market=None 的分母含非研報（讀得到 tags 快取的 `not_research`），兩群每天的新檔數不大，"
          "差值 CI 很寬、常判「未定」屬預期；`skip_non_research` 同理。"]

    skip = rep["skip_list"]
    L += ["", "## 跳過名單（切換後新增，`research.llm_task_failure`）", ""]
    if not skip["table_ready"]:
        L.append("表不存在。")
    elif not skip["by_task_reason"]:
        L.append("沒有。")
    else:
        L += ["| task | reason | 筆數 | 已被跳過 |", "|---|---|---|---|"]
        for it in skip["by_task_reason"]:
            L.append(f"| {it['task']} | {it['reason']} | {it['count']} | {it['skipping']} |")
        L += ["", "逐筆看 `make llm-blocked`。"]

    L += _render_usage(rep)

    L += ["", "## 限制", ""]
    L += [f"- {x}" for x in rep["limitations"]]
    L.append("- 分群依據與各任務的已知偏差見本檔模組 docstring。")
    L += ["", "## 未涵蓋項目（批次 A／B 觀測條件裡本報告量不到的）", ""]
    L += [f"- 未涵蓋：{x}" for x in rep["uncovered"]]
    return "\n".join(L) + "\n"


def _render_usage(rep: dict) -> list[str]:
    h = rep["usage_health"]
    L = ["", "## 用量紀錄判準（批次 A；切換後 DeepSeek HTTP 呼叫）", ""]
    L += ["| 指標 | 判準 | 值 | 判讀 |", "|---|---|---|---|"]
    for v in rep["usage_verdicts"]:
        val = v["value"]
        if isinstance(val, dict) and "rate" in val:
            shown = f"{_p(val)}，涉及 {val['reports']} 篇"
        elif isinstance(val, dict):  # 斷路器標記
            shown = ("沒有標記檔" if not val.get("exists")
                     else f"ts={val['ts'].isoformat() if val.get('ts') else '?'}；reason={val.get('reason')}")
        else:
            shown = "—" if val is None else str(val)
        L.append(f"| {v['metric']} | {v['criterion']} | {shown} | **{v['verdict']}** |")
    if not h["available"]:
        return L + ["", "沒有用量紀錄，以上計數都是樣本不足。"]
    L += ["", "content_filter 一次都沒有時，約要 381 次呼叫 Wilson 上界才會 ≤ 1%；呼叫數少的 task 判「未定」屬預期。"]
    tc = h["truncation_skip_check"]
    L += ["", f"截斷對照跳過名單：已記入 {tc['recorded']}、之後成功 {tc['later_success']}、"
              f"**沒記入也沒成功 {len(tc['unrecorded'])}**、無 file_hash（簡報）{tc['no_file_hash']}、"
              f"跳過名單表不存在無法對照 {tc['unknown']}。"]
    for it in tc["unrecorded"][:20]:
        L.append(f"- `{it['task']}` `{it['file_hash']}`")
    L += ["", "各 task 的 kind 分布與期限型截斷（`timeout_streamed`，可重放、只列觀測）：", "",
          "| task | 呼叫數 | timeout_streamed | kind 分布 |", "|---|---|---|---|"]
    for task, t in h["tasks"].items():
        L.append(f"| {task} | {t['calls']} | {t['timeout_streamed']} | {t['kinds']} |")
    L += ["", "每日 token（台北日期；觀測，金額看 DeepSeek 後台／`/healthz/llm`）：", "",
          "| 日期 | 呼叫數 | 快取命中 | 未命中 | 輸出 | 推理 |", "|---|---|---|---|---|---|"]
    for day, d in h["daily_tokens"].items():
        L.append(f"| {day} | {d.get('calls', 0)} | {d.get('hit', 0)} | {d.get('miss', 0)} | "
                 f"{d.get('completion', 0)} | {d.get('reasoning', 0)} |")
    return L


def _miss(p: dict) -> dict:
    n, k = p["n"], p["k"]
    return prop(n - k, n)


def _len(b: Optional[dict]) -> str:
    return "—" if not b else f"{b['p10']:.0f}／{b['p50']:.0f}／{b['p90']:.0f}（n={b['n']}）"


def _num_diff(d: Optional[dict]) -> str:
    return "—" if d is None else f"{d['point']:+.2f} [{d['ci'][0]:+.2f}, {d['ci'][1]:+.2f}]"


# ── CLI ──────────────────────────────────────────────────────────────────────


def parse_switch_at(value: str) -> datetime:
    ts = datetime.fromisoformat(value)
    return ts if ts.tzinfo else ts.replace(tzinfo=TAIPEI)


def repo_roots(root: Path = ROOT) -> list[Path]:
    """本 checkout 與（在 worktree 裡時）主 checkout 的根目錄。只讀 `.git` 檔，不跑 git。

    worktree 的 `.git` 是一行 `gitdir: <主 checkout>/.git/worktrees/<名稱>`；主 checkout 就是部署目錄。
    """
    roots = [root.resolve()]
    git = root / ".git"
    if git.is_file():
        try:
            line = git.read_text(encoding="utf-8").strip()
        except OSError:
            line = ""
        if line.startswith("gitdir:"):
            gitdir = Path(line[len("gitdir:"):].strip())
            gitdir = (gitdir if gitdir.is_absolute() else root / gitdir).resolve()
            if gitdir.parent.name == "worktrees" and gitdir.parent.parent.name == ".git":
                roots.append(gitdir.parent.parent.parent)
    return roots


def _out_allowed(path: Path) -> bool:
    """`--out` 只接受 repo 外的路徑：本 checkout 與主 checkout（部署目錄）底下一律拒收。

    只擋 `data/` 不夠：repo 內任何已追蹤檔案（README、eval 基準線……）都可能被蓋掉；
    逐一問 git 哪些檔案有追蹤要跑子行程，不如整個 repo 都不收，輸出本來就該放 /tmp 或家目錄。
    """
    resolved = path.resolve()
    return all(resolved != r and r not in resolved.parents for r in repo_roots())


def _deploy_hint(default: Path, given: Path) -> Optional[str]:
    """用預設路徑、檔案不存在、又是從 worktree 跑：提示部署目錄裡的對應路徑。"""
    roots = repo_roots()
    if given != default or given.exists() or len(roots) < 2:
        return None
    return str(roots[1] / default.relative_to(ROOT))


def _json_default(o):
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(type(o).__name__)


def render_dry_run(window: Window, usage: dict[tuple[str, str], UsageHit], usage_stats: dict) -> str:
    L = [f"-- dry-run：不連 DB。用量紀錄 {usage_stats}", ""]
    for name, sql, params in query_plan(window, usage):
        shown = {k: (f"<{len(v)} 個 file_hash>" if isinstance(v, list) else v) for k, v in params.items()}
        L += [f"-- [{name}] 參數 {json.dumps(shown, ensure_ascii=False, default=_json_default)}", sql + ";", ""]
    L.append("-- 之後 rollback；不寫任何表。")
    return "\n".join(L) + "\n"


def main(argv: Optional[Sequence[str]] = None, *, session_factory=None) -> int:
    ap = argparse.ArgumentParser(description="DeepSeek 切換後批次產出觀測（零 LLM、唯讀；只輸出判讀）")
    ap.add_argument("--switch-at", required=True, help="切換時點（ISO；沒帶時區視為台北時間）")
    ap.add_argument("--before-days", type=int, default=30, help="Claude 對照窗期：切換前幾天（預設 30）")
    ap.add_argument("--claude-until", default=DEFAULT_CLAUDE_UNTIL,
                    help=f"Claude 群的截止（CLI 失效時點；到 --switch-at 之間是事故空窗、兩群都不收；"
                         f"預設 {DEFAULT_CLAUDE_UNTIL}）")
    ap.add_argument("--until", default=None, help="觀測截止（ISO；預設現在）")
    ap.add_argument("--grace-hours", type=float, default=6.0,
                    help="缺值率不計最近幾小時入庫的研報（下游批次還沒輪到；預設 6）")
    ap.add_argument("--usage-log", default=str(DEFAULT_USAGE_LOG),
                    help="用量紀錄（只讀；不存在就只依時間分群。從 worktree 跑時指到部署目錄的 data/llm_usage.jsonl）")
    ap.add_argument("--tags-dir", default=str(DEFAULT_TAGS_DIR), help="tags 快取目錄（只讀；worktree 同上）")
    ap.add_argument("--breaker-file", default=str(DEFAULT_BREAKER_FILE),
                    help="批次斷路器標記（只讀；worktree 同上）")
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260925)
    ap.add_argument("--json", action="store_true", help="輸出機讀 JSON（預設 markdown）")
    ap.add_argument("--out", default=None, help="輸出檔（預設 stdout；只接受 repo 外的路徑）")
    ap.add_argument("--dry-run", action="store_true", help="只印會跑的查詢，不連 DB（不檢查 --until 是否晚於切換）")
    args = ap.parse_args(argv)

    try:
        switch_at = parse_switch_at(args.switch_at)
        claude_until = parse_switch_at(args.claude_until)
        until = parse_switch_at(args.until) if args.until else datetime.now(timezone.utc)
    except ValueError as exc:
        print(f"時間格式錯誤：{exc}", file=sys.stderr)
        return RC_CONFIG
    if args.before_days <= 0:
        print("--before-days 必須 > 0", file=sys.stderr)
        return RC_CONFIG
    if until <= switch_at:
        if not args.dry_run:
            print("--until 必須晚於 --switch-at（切換還沒開始就沒有 DeepSeek 產出可比）", file=sys.stderr)
            return RC_CONFIG
        # dry-run 只印查詢：切換時點在未來也可以先看查詢長什麼樣
        until = switch_at + timedelta(seconds=1)
    if args.out and not _out_allowed(Path(args.out)):
        print(f"--out 只接受 repo 外的路徑（{'、'.join(str(r) for r in repo_roots())} 底下一律拒收）",
              file=sys.stderr)
        return RC_CONFIG

    window = Window(switch_at=switch_at, before_start=switch_at - timedelta(days=args.before_days), until=until,
                    grace=timedelta(hours=args.grace_hours), claude_until=claude_until)
    usage_path = Path(args.usage_log) if args.usage_log else None
    for default, given, flag in ((DEFAULT_USAGE_LOG, usage_path, "--usage-log"),
                                 (DEFAULT_TAGS_DIR, Path(args.tags_dir), "--tags-dir"),
                                 (DEFAULT_BREAKER_FILE, Path(args.breaker_file), "--breaker-file")):
        hint = _deploy_hint(default, given) if given is not None else None
        if hint:
            print(f"注意：從 worktree 跑、{given} 不存在；生產資料在 {flag} {hint}", file=sys.stderr)
    usage, usage_stats = load_usage(usage_path, [TASK_TAG, TASK_TITLE, TASK_SUMMARY, TASK_TAKEAWAY, TASK_SIGNAL])

    if args.dry_run:
        output = render_dry_run(window, usage, usage_stats)
    else:
        data = asyncio.run(fetch(window, usage, session_factory=session_factory))
        health_rows, health_stats = read_usage_rows(usage_path, window)
        rep = build_report(data, window, usage, usage_stats, tag_lookup=tags_lookup_from(Path(args.tags_dir)),
                           n_boot=args.bootstrap, seed=args.seed, health_rows=health_rows,
                           health_stats=health_stats, breaker=read_breaker(Path(args.breaker_file)))
        output = (json.dumps(rep, ensure_ascii=False, indent=2, default=_json_default) + "\n") if args.json \
            else render_markdown(rep)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"已寫入 {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(output)
    return RC_OK


if __name__ == "__main__":
    raise SystemExit(main())
