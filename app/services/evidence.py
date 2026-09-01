"""M4b 共用證據帳本：問答與研報共用的來源身分資料契約。

職責僅限「來源身分、去重、持久化（manifest 序列化）與引用渲染」；
「主張是否被支持」是 M8 grounding 的責任——存在引用不得被解讀為真實性。

evidence_id 為內容定址（sha256 前 16 hex）、不可變：同一來源在問答與研報、
跨 session 恆得同一 id，去重天然成立。內部（M5/M7 逐節生成）只寫
`[[ev:<id>]]` 佔位，最終由 render_citations 一次性映射為 `[n]`——
編號穩定性來自「id 不變 + 單次最終渲染」。

外部來源只能經受控建構器產生：from_trusted_point（M4a adapter）與
from_ext_source（研報／問答的受控 Web 解析結果）；其他路徑不得自行拼
external dict。設計 spec 見 git 歷史 docs/superpowers/specs/2026-07-14-m4b-evidence-ledger-design.md
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from typing import Iterator, Literal, NamedTuple

EVIDENCE_SCHEMA_VERSION = 1

Kind = Literal["corpus", "external"]

EV_PLACEHOLDER_RE = re.compile(r"\[\[ev:([0-9a-f]{8,64})\]\]")
# 寬鬆掃描：合法替換後殘留的任何 [[ev:...]] 形狀（大寫/非 hex/過短/空 id）
# 一律視為未知並移除——內部 token 不得漏到輸出（模型抄寫 id 漂移是常見失效）。
_EV_MALFORMED_RE = re.compile(r"\[\[ev:[^\]]*\]\]")


class EvidenceValidationError(ValueError):
    """manifest 形狀/身分驗證失敗（嚴格讀取路徑用；寬鬆讀取見 EvidenceLedger.load）。"""


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    kind: Kind
    # corpus 來源
    report_id: str | None = None
    chunk_id: str | None = None
    file_name: str | None = None
    market: str | None = None
    report_date: str | None = None
    # 外部來源
    url: str | None = None
    title: str | None = None
    source_type: str | None = None
    profile_id: str | None = None
    published_at: str | None = None
    retrieved_at: str | None = None
    snapshot_ref: str | None = None
    content_hash: str | None = None


_FIELD_NAMES = tuple(f.name for f in fields(Evidence))


def _derive_id(kind: str, *parts: str) -> str:
    payload = "\x00".join([kind, *[p or "" for p in parts]])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _expected_id(d: dict) -> str | None:
    """依身分欄位重算 evidence_id；kind 不明回 None。"""
    kind = d.get("kind")
    if kind == "corpus":
        return _derive_id("corpus", d.get("report_id") or "", d.get("chunk_id") or "")
    if kind == "external":
        return _derive_id("external", d.get("url") or "", d.get("content_hash") or "")
    return None


class EvidenceLedger:
    """插入序、以 evidence_id 去重（首次登錄勝，符合不可變語義）。"""

    def __init__(self) -> None:
        self._items: dict[str, Evidence] = {}

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Evidence]:
        return iter(self._items.values())

    def get(self, evidence_id: str) -> Evidence | None:
        return self._items.get(evidence_id)

    def _add(self, evidence: Evidence) -> Evidence:
        existing = self._items.get(evidence.evidence_id)
        if existing is not None:
            return existing
        self._items[evidence.evidence_id] = evidence
        return evidence

    def add_corpus(
        self,
        *,
        report_id: str,
        chunk_id: str | None = None,
        file_name: str | None = None,
        market: str | None = None,
        report_date: str | None = None,
    ) -> Evidence:
        return self._add(
            Evidence(
                evidence_id=_derive_id("corpus", report_id, chunk_id or ""),
                kind="corpus",
                report_id=report_id,
                chunk_id=chunk_id,
                file_name=file_name,
                market=market,
                report_date=report_date,
            )
        )

    def add_external(
        self,
        *,
        url: str,
        title: str | None = None,
        source_type: str | None = "web",
        profile_id: str | None = None,
        published_at: str | None = None,
        retrieved_at: str | None = None,
        snapshot_ref: str | None = None,
        content_hash: str | None = None,
        canonical_payload: bytes | None = None,
    ) -> Evidence:
        return self._add(from_ext_source({
            "url": url, "title": title, "source_type": source_type,
            "profile_id": profile_id, "published_at": published_at,
            "snapshot_ref": snapshot_ref, "content_hash": content_hash,
            "canonical_payload": canonical_payload,
        }, retrieved_at))

    def add(self, evidence: Evidence) -> Evidence:
        """登錄一筆已由受控建構器產生的 Evidence（去重）。"""
        return self._add(evidence)

    def merge(self, other: "EvidenceLedger") -> None:
        for e in other:
            self._add(e)

    def to_manifest(self) -> dict:
        return {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "evidence": [asdict(e) for e in self],
        }

    @classmethod
    def from_manifest(cls, obj) -> "EvidenceLedger":
        """嚴格讀取：形狀/身分驗證失敗 raise EvidenceValidationError。"""
        errors = validate_manifest(obj)
        if errors:
            raise EvidenceValidationError("; ".join(errors))
        led = cls()
        for d in obj.get("evidence", []):
            led._add(Evidence(**{k: d.get(k) for k in _FIELD_NAMES}))
        return led

    @staticmethod
    def load(obj) -> "EvidenceLedger":
        """寬鬆讀取：None/{}/壞形狀 → 空帳本（歷史列 NULL manifest 的安全退化）。"""
        try:
            return EvidenceLedger.from_manifest(obj)
        except Exception:  # noqa: BLE001 — 讀取路徑 fail-open，不擋歷史重現
            return EvidenceLedger()


def validate_manifest(obj) -> list[str]:
    """回錯誤清單（空＝通過）：schema_version、kind、必要欄位、id 一致性、重複。"""
    if not isinstance(obj, dict):
        return ["manifest must be a dict"]
    errors: list[str] = []
    if obj.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        errors.append(f"unsupported schema_version: {obj.get('schema_version')!r}")
    items = obj.get("evidence")
    if not isinstance(items, list):
        return errors + ["evidence must be a list"]
    seen: set[str] = set()
    for i, d in enumerate(items):
        if not isinstance(d, dict):
            errors.append(f"evidence[{i}] must be a dict")
            continue
        kind = d.get("kind")
        if kind not in ("corpus", "external"):
            errors.append(f"evidence[{i}] has invalid kind: {kind!r}")
            continue
        if kind == "corpus" and not d.get("report_id"):
            errors.append(f"evidence[{i}] corpus requires report_id")
            continue
        if kind == "external":
            url = d.get("url") or ""
            if not (url.startswith("http://") or url.startswith("https://")):
                errors.append(f"evidence[{i}] external requires http(s) url")
                continue
            if not d.get("profile_id"):
                errors.append(f"evidence[{i}] external requires profile_id")
                continue
            if not d.get("snapshot_ref"):
                errors.append(f"evidence[{i}] external requires snapshot_ref")
                continue
            content_hash = d.get("content_hash") or ""
            if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
                errors.append(f"evidence[{i}] external requires sha256 content_hash")
                continue
        expected = _expected_id(d)
        if d.get("evidence_id") != expected:
            errors.append(
                f"evidence[{i}] evidence_id mismatch (tampered or hand-built)"
            )
            continue
        if d["evidence_id"] in seen:
            errors.append(f"evidence[{i}] duplicate evidence_id")
            continue
        seen.add(d["evidence_id"])
    return errors


class RenderedCitations(NamedTuple):
    text: str
    ordered: list[Evidence]          # 依首次出現序
    number_of: dict[str, int]        # evidence_id → [n]
    n_unknown: int                   # 未知 id 的佔位數（M7 組裝時必須為 0）


def render_citations(text: str, ledger: EvidenceLedger) -> RenderedCitations:
    """`[[ev:<id>]]` → `[n]`：依首次出現序分配 1..N，同一 evidence 全文恆同號。

    未知 id 的佔位一律移除並計數（內部 token 不得漏到輸出）。
    """
    number_of: dict[str, int] = {}
    ordered: list[Evidence] = []
    unknown = 0

    def _sub(m: re.Match) -> str:
        nonlocal unknown
        eid = m.group(1)
        e = ledger.get(eid)
        if e is None:
            unknown += 1
            return ""
        n = number_of.get(eid)
        if n is None:
            n = len(ordered) + 1
            number_of[eid] = n
            ordered.append(e)
        return f"[{n}]"

    rendered = EV_PLACEHOLDER_RE.sub(_sub, text or "")

    # 二段式清理：合法形狀已處理完，殘留的變形佔位（大寫/非 hex/過短）同樣
    # 移除並計入 unknown，確保「不漏內部 token」與把關訊號同時成立。
    def _sub_malformed(m: re.Match) -> str:
        nonlocal unknown
        unknown += 1
        return ""

    rendered = _EV_MALFORMED_RE.sub(_sub_malformed, rendered)
    return RenderedCitations(rendered, ordered, number_of, unknown)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def from_trusted_point(point, retrieved_at: str | None = None) -> Evidence:
    """M4a TrustedDataPoint → external Evidence（受控建構器）。"""
    return Evidence(
        evidence_id=_derive_id("external", point.url, point.content_hash or ""),
        kind="external",
        url=point.url,
        title=f"{point.provider}（{point.source_type}）",
        source_type=point.source_type,
        profile_id=point.profile_id,
        published_at=(
            point.published_at.isoformat() if point.published_at else None
        ),
        retrieved_at=retrieved_at or _now_iso(),
        snapshot_ref=point.snapshot_ref,
        content_hash=point.content_hash,
    )


def from_ext_source(d: dict, retrieved_at: str | None) -> Evidence:
    """已由受控 adapter 擷取、快照化的外部來源 → external Evidence。

    僅有模型輸出的 title/url 不足以構成證據；adapter 必須提供 profile、不可變
    快照參照與可重算的 canonical payload hash。非受控形狀直接 raise。
    """
    url = (d.get("url") or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError(f"external source requires http(s) url: {url!r}")
    profile_id = d.get("profile_id")
    snapshot_ref = d.get("snapshot_ref")
    payload = d.get("canonical_payload")
    content_hash = d.get("content_hash")
    if not profile_id or not snapshot_ref:
        raise ValueError("external source requires profile_id and snapshot_ref")
    if not isinstance(payload, bytes):
        raise ValueError("external source canonical_payload must be bytes")
    if hashlib.sha256(payload).hexdigest() != content_hash:
        raise ValueError("external source content_hash mismatch")
    return Evidence(
        evidence_id=_derive_id("external", url, content_hash),
        kind="external",
        url=url,
        title=d.get("title"),
        source_type=d.get("source_type") or "web",
        profile_id=profile_id,
        published_at=d.get("published_at"),
        retrieved_at=retrieved_at,
        snapshot_ref=snapshot_ref,
        content_hash=content_hash,
    )


def manifest_from_answer(
    sources: list[dict],
    ext_sources: list[dict],
    *,
    retrieved_at: str | None,
) -> dict | None:
    """問答/研報寫入路徑共用：sources 與受控 adapter 來源 → manifest。

    無任何證據回 None（寫 NULL，與歷史列同語義）。模型的 [EXT_SOURCES]／研報
    Markdown 只有 title/url，缺少 adapter 快照與 hash，會被逐筆跳過而不污染帳本。
    """
    led = EvidenceLedger()
    for s in sources or []:
        rid = s.get("report_id")
        if not rid:
            continue
        led.add_corpus(
            report_id=str(rid),
            file_name=s.get("file_name"),
            market=s.get("market"),
            report_date=s.get("report_date"),
        )
    for d in ext_sources or []:
        try:
            led.add(from_ext_source(d, retrieved_at))
        except (ValueError, AttributeError, TypeError):
            continue
    if len(led) == 0:
        return None
    return led.to_manifest()
