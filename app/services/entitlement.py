"""API 用戶端的研報 entitlement（allowlist）：哪些研報對這個用戶端「存在」。

四個維度，每個維度內是 OR、維度之間是 AND：

- `markets`：必填、非空（findb 市場代碼，對照 `app/services/tagging.py`）。
- `sources`／`report_types`／`instrument_types`：`None`＝該維度不限。

**過濾一律下推到 SQL**（`sql()`，由 `store.search_chunks_meta`／`store.search_chunks_lexical`
拼進 WHERE）：在 Python 事後過濾會讓 dense 的 `LIMIT :scan` 與字面路的 `LIMIT :cap`
名額被不可見的研報吃掉，k 與召回數一起偏掉。`allows()` 是同一組語意的 Python 版，
給逐篇判斷（例如原檔連結）用；兩者逐項等價由 `tests/test_entitlement.py` 以同一張
表格案例兩邊各驗一次。

NULL 的處理是 fail-closed：某維度有設定時，研報在該維度為 NULL（`source`／
`report_type` 未知、`instrument_types` 為 NULL 或空陣列）一律視為不符——SQL 端
`NULL = ANY(...)`、`NULL && ...` 本來就不為真，Python 端照抄同一個結論。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence

# from_mapping 接受的鍵（單數，對應管理後台與 DB 的維度名）→ dataclass 欄位
_KEYS = {
    "market": "markets",
    "source": "sources",
    "report_type": "report_types",
    "instrument_type": "instrument_types",
}
_ALIAS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _normalize(key: str, values: Sequence[str]) -> tuple[str, ...]:
    # 單一字串也是 Sequence[str]，不擋的話 "TW" 會被拆成 ("T", "W")。
    if isinstance(values, (str, bytes)):
        raise ValueError(f"entitlement {key} 必須是字串清單，不是單一字串")
    out: list[str] = []
    for v in values:
        if not isinstance(v, str) or not v:
            raise ValueError(f"entitlement {key} 的值必須是非空字串：{v!r}")
        out.append(v)
    return tuple(dict.fromkeys(out))


@dataclass(frozen=True)
class Entitlement:
    markets: tuple[str, ...]  # 必填、非空
    sources: tuple[str, ...] | None = None  # None＝不限
    report_types: tuple[str, ...] | None = None
    instrument_types: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not self.markets:
            raise ValueError("entitlement 的 market 必填且不可為空")
        # 直接建構時的空 tuple 語意是「一篇都不符」，與 from_mapping 的「空清單＝不限」
        # 相反；兩種意思並存只會讓呼叫端寫錯，一律要求用 None 表示不限。
        for name in ("sources", "report_types", "instrument_types"):
            value = getattr(self, name)
            if value is not None and not value:
                raise ValueError(f"entitlement 的 {name} 不限時請用 None，不要給空清單")

    @classmethod
    def from_mapping(cls, m: Mapping[str, Sequence[str]]) -> "Entitlement":
        """`{"market": [...], "source": [...], ...}` → Entitlement。

        market 缺或空 → ValueError；其他鍵缺或空清單 → None（不限）。
        **不認得的鍵一律 ValueError**：打錯維度名（`markets`、`broker`）若被默默忽略，
        那個維度就變成不限——allowlist 打錯字不能等於放寬權限。
        """
        unknown = sorted(set(m) - set(_KEYS))
        if unknown:
            raise ValueError(f"entitlement 有不認得的維度：{', '.join(unknown)}")
        fields: dict[str, tuple[str, ...] | None] = {}
        for key, field_name in _KEYS.items():
            raw = m.get(key)
            values = _normalize(key, raw) if raw is not None else ()
            fields[field_name] = values or None
        markets = fields["markets"]
        if not markets:
            raise ValueError("entitlement 的 market 必填且不可為空")
        return cls(
            markets=markets,
            sources=fields["sources"],
            report_types=fields["report_types"],
            instrument_types=fields["instrument_types"],
        )

    def sql(self, alias: str = "r") -> tuple[str, dict[str, list[str]]]:
        """回傳以 AND 串起的 WHERE 條件與綁定參數（參數名一律 `ent_` 前綴）。

        `alias` 是該查詢中 `research.research_report` 的別名。寫法都吃得到索引：
        純量欄位 `= ANY(CAST(... AS text[]))` 走 B-tree（`idx_research_report_market`、
        `idx_rr_source`、`idx_rr_report_type`），陣列欄位用 `&&` 走 GIN
        （`= ANY(<陣列欄位>)` 才是吃不到 GIN 的寫法，見 `tests/test_sql_index_hygiene.py`）。
        """
        if not _ALIAS_RE.match(alias):
            raise ValueError(f"不合法的資料表別名：{alias!r}")
        conds = [f"{alias}.market = ANY(CAST(:ent_markets AS text[]))"]
        params: dict[str, list[str]] = {"ent_markets": list(self.markets)}
        if self.sources is not None:
            conds.append(f"{alias}.source = ANY(CAST(:ent_sources AS text[]))")
            params["ent_sources"] = list(self.sources)
        if self.report_types is not None:
            conds.append(f"{alias}.report_type = ANY(CAST(:ent_report_types AS text[]))")
            params["ent_report_types"] = list(self.report_types)
        if self.instrument_types is not None:
            conds.append(f"{alias}.instrument_types && CAST(:ent_instrument_types AS text[])")
            params["ent_instrument_types"] = list(self.instrument_types)
        return " AND ".join(conds), params

    def allows(
        self,
        *,
        market: str | None,
        source: str | None,
        report_type: str | None,
        instrument_types: Sequence[str] | None,
    ) -> bool:
        """與 `sql()` 語意逐項等價的 Python 判斷（NULL 在有設定的維度一律不符）。"""
        if market is None or market not in self.markets:
            return False
        if self.sources is not None and (source is None or source not in self.sources):
            return False
        if self.report_types is not None and (
            report_type is None or report_type not in self.report_types
        ):
            return False
        if self.instrument_types is not None and not (
            set(instrument_types or ()) & set(self.instrument_types)
        ):
            return False
        return True
