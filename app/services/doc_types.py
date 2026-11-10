"""文件類型（`research_report.doc_type`，revision 0012）與每種類型啟用的功能。

改動前只有布林 `is_research`（false＝使用者端完全看不到），各功能各寫一份 `is_research IS NOT FALSE`。
現在改成「文件類型＋每種類型啟用哪些功能」：類型與功能的對照在 profile 的 `[doc_types]`
（`app/profile.py`），SQL 一律經這裡產生的片段，新增類型只需 profile 設定＋該類型的標註判斷，
不必再改各功能的 SQL。

## 片段

- `doc_type_enabled_sql(別名, 功能…)`：`別名` 指向 research.research_report → 「這份文件的類型啟用了
  任一個指定功能」的布林運算式（`r.doc_type IN ('research')`；沒有任何類型啟用時是 `FALSE`）。
  形狀比照 `visibility.visible_report_sql`：單一布林運算式，可以直接 AND 進任何 WHERE、也能放在
  SELECT 欄位裡（雷達的 `listed`），不改 FROM。
- `doc_type_enabled_id_sql(report_id 欄位, 功能…)`：只有 report_id 可用的查詢（例如只查 report_signal
  的簡報變動）→ 同語意的 `EXISTS (...)`，多一次 research_report 主鍵查找。

**片段在呼叫時才依 profile 渲染**，所以用到它的 SQL 不可做成 import 期定值的模組常數（會留著上一個
profile 的類型清單，而且 import 就讀 profile）：一律寫成函式（例如 `reading/queries.py` 的 `_doc_sql()`）。
類型名稱已由 profile 驗證成 `[a-z][a-z0-9_]*`，以字面值拼進 SQL 是安全的；刻意不用 bind 參數，
是為了讓呼叫端不必另外傳參數、既有 SQL 的參數表不變。

## 功能管到哪些查詢

- `search`：混合檢索兩路與純中文重探（檢索頁、問答、對外 API 共用：`store.search_chunks_meta`／
  `_lexical_sql`／`pick_title_lead_term`）、檢索頁瀏覽（`store.list_reports`）、對外 API 的原檔連結、
  `scripts/measure_baseline.py` 的母體。
- `reading`：閱讀頁（單篇、訊號、標的名稱、chunk 內文、相似研報）；原檔 presign 是 `reading` 或 `search`
  任一（閱讀頁與檢索結果都點得到原檔）。
- `tagging_markets`：以標準市場代碼標註——入庫閘要求 market 非空（`ingest_gate`）；封面索引表修復的候選。
- `summary`／`title`／`takeaway`／`signal`：對應批次挑候選、監控頁覆蓋率；`summary` 兼批次新鮮度的語料閘。
- `radar`：觀點雷達全部查詢。
- `brief`：每日簡報的素材、篇數、市場分布、評等變動與來源連結。
- `overview`：總覽題的分面聚合。

問答沒有獨立的功能：它與檢索頁共用混合檢索（`search`），而 `hybrid_search` 的簽章刻意不為它多一個參數
（理由見 `retrieval.hybrid_search` 的 docstring）。

## 入庫閘

`ingest_gate(類型, market)`：類型至少啟用一個功能才入庫；啟用 `tagging_markets` 時另要求 market 非空。
預設 profile 下等同改動前的 `is_research and market`（非研究檔全關→不入庫、記 `not_research`）。

## 與 `is_research` 的關係

`is_research` 欄位 v1 保留（舊版程式與備份還原仍讀得懂），寫入路徑一律由 `doc_type` 推得
（`legacy_is_research`：只有 `research` 是 true），revision 0012 的 CHECK 約束保證兩欄一致。
讀取一律走這裡的片段，**不要再寫 `is_research` 過濾**（`tests/test_doc_type_guard.py` 掃描守門）。
管理面刻意不套類型過濾（`visibility.list_reports` 與 `web/stats_snapshot.py` 的券商分佈），理由見各自的註解。
"""

from __future__ import annotations

import re

from app.profile import DOC_FEATURES, get_profile
from app.services.tagging import DOC_TYPE_RESEARCH

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COLUMN_EXPR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")
_TYPE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def _check_features(features: tuple[str, ...], caller: str) -> None:
    if not features:
        raise ValueError(f"{caller}：至少要指定一個功能")
    for f in features:
        if f not in DOC_FEATURES:
            raise ValueError(f"{caller}：未知的功能 {f!r}（可用：{'、'.join(DOC_FEATURES)}）")


def enabled_types(*features: str) -> tuple[str, ...]:
    """啟用了任一指定功能的類型（依 profile 的宣告順序）。"""
    _check_features(features, "enabled_types")
    doc_types = get_profile().doc_types
    return tuple(t for t in doc_types.features if any(doc_types.enabled(t, f) for f in features))


def _in_list(column: str, features: tuple[str, ...], caller: str) -> str:
    _check_features(features, caller)
    types = enabled_types(*features)
    if not types:
        return "FALSE"
    for t in types:  # profile 已驗證過；拼進 SQL 的東西仍在這裡再擋一次
        if not _TYPE_NAME.match(t):
            raise ValueError(f"{caller}：不合法的類型名稱 {t!r}")
    return f"{column} IN (" + ", ".join(f"'{t}'" for t in types) + ")"


def doc_type_enabled_sql(alias: str, *features: str) -> str:
    """`alias` 指向 research.research_report → 「類型啟用了任一指定功能」的布林運算式。"""
    if not _IDENT.match(alias):
        raise ValueError(f"doc_type_enabled_sql：不合法的別名 {alias!r}")
    return _in_list(f"{alias}.doc_type", features, "doc_type_enabled_sql")


def doc_type_enabled_id_sql(report_id_expr: str, *features: str) -> str:
    """只有 report_id 可用的查詢 → 同語意的 `EXISTS (...)`（多一次 research_report 主鍵查找）。"""
    if not _COLUMN_EXPR.match(report_id_expr):
        raise ValueError(f"doc_type_enabled_id_sql：不合法的欄位運算式 {report_id_expr!r}")
    cond = _in_list("dt_r.doc_type", features, "doc_type_enabled_id_sql")
    return (
        "EXISTS (SELECT 1 FROM research.research_report dt_r "
        f"WHERE dt_r.id = {report_id_expr} AND {cond})"
    )


def is_enabled(doc_type: str | None, feature: str) -> bool:
    """Python 端的同一個判斷（例如入庫閘）。"""
    _check_features((feature,), "is_enabled")
    return get_profile().doc_types.enabled(doc_type, feature)


def ingest_gate(doc_type: str | None, market: str | None) -> bool:
    """這份文件要不要入庫：類型至少啟用一個功能；啟用 `tagging_markets` 時 market 必須非空。"""
    feats = get_profile().doc_types.features.get(doc_type or "", frozenset())
    if not feats:
        return False
    if "tagging_markets" in feats and not market:
        return False
    return True


def legacy_is_research(doc_type: str) -> bool:
    """寫入舊欄位 `is_research` 的值（revision 0012 的 CHECK：`is_research = (doc_type = 'research')`）。"""
    return doc_type == DOC_TYPE_RESEARCH
