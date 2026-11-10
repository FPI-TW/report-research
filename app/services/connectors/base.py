"""連接器共用的部分：候選檔掃描、側檔配對與「處理過了」的狀態檔。

每一種連接器都先把新檔「落地」成本機資料夾裡的檔案（folder 本來就在本機；s3 與 plugin 由 `fetch()`
拉下來），之後的掃描、側檔、狀態、入庫完全共用：

1. `scan()` 列出資料夾裡的研報檔（`EXTS`），配上側檔（`<主檔名>.json` 或根目錄的 `_metadata.csv`），
   算出指紋（大小、mtime、側檔內容）。指紋與狀態檔裡記的相同＝處理過了，略過。
2. 呼叫端逐篇交給 `scripts/_ingest_core.ingest_one`（以 file_hash 去重：已在庫的回 `skip_exists`）。
3. 結果是「重跑不會不一樣」的（`TERMINAL_KINDS`）才記進狀態檔；`fail`、`skip_untagged`、`missing`
   不記，下一輪重試。所以狀態檔遺失或被刪，最壞只是重抽一次字、全部 `skip_exists`，不會重複入庫。

狀態檔是 JSON，原子改名寫入（`<CONNECTOR_DATA_DIR>/state/<連接器名>.json`）。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.connectors.sidecar import (
    MANIFEST_NAME,
    SIDECAR_SUFFIX,
    Manifest,
    Metadata,
    SidecarError,
    parse_json_bytes,
    parse_manifest,
    sidecar_path,
)

# 研報檔的副檔名（與 scripts/sync_new_reports.py 的 EXTS 相同）。
EXTS = frozenset({".pdf", ".docx", ".doc"})
# 落地中的暫存檔：s3 拉檔先寫 `.<名>.part`，一般複製工具也常用這些字尾。
_PARTIAL_SUFFIXES = (".part", ".tmp", ".crdownload")

# `ingest_one` 的結果裡「重跑不會不一樣」的：記進狀態檔，下一輪不再送。其餘（fail、skip_untagged、missing）
# 下一輪重試。skip_blocked／skip_truncated 重送是同一份輸入、同一個上限，只會再付一次錢。
TERMINAL_KINDS = frozenset({
    "ingested", "skip_exists", "skip_admin", "skip_scanned", "skip_non_research", "skip_blocked", "skip_truncated",
})
# 側檔不合格：檔案不入庫、進失敗清單；指紋含側檔內容，提供方改好側檔下一輪就會重送。
SIDECAR_INVALID = "sidecar_invalid"


@dataclass(frozen=True)
class FetchReport:
    """`fetch()` 的結果：新落地幾檔、失敗幾檔、失敗原因（給人看）。"""

    landed: int = 0
    failed: int = 0
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class Candidate:
    rel: str  # 相對於根目錄的路徑（`/` 分隔），狀態檔的鍵
    path: Path
    fingerprint: str
    metadata: Metadata | None = None
    sidecar_error: str | None = None


def _fingerprint(path: Path, sidecar_digest: str) -> str:
    st = path.stat()
    return f"{st.st_size}:{st.st_mtime_ns}:{sidecar_digest}"


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:16]


def _is_candidate(path: Path) -> bool:
    name = path.name
    return (path.suffix.lower() in EXTS and not name.startswith(".")
            and not name.lower().endswith(_PARTIAL_SUFFIXES))


def scan(root: Path, *, settle_seconds: int = 0, now: float | None = None) -> list[Candidate]:
    """列出 root 底下所有研報檔與其側檔，依相對路徑排序。

    mtime 距今未滿 `settle_seconds` 的檔先不列（可能還在複製中）；它的側檔同理以研報檔為準一起延後。
    `_metadata.csv` 不合格時，root 底下每一份檔都帶著同一個錯誤（整份清單不採信，見 sidecar.parse_manifest）。
    """
    now = time.time() if now is None else now
    if not root.is_dir():
        return []
    manifest: Manifest | None = None
    manifest_error: str | None = None
    manifest_digest = ""
    manifest_path = root / MANIFEST_NAME
    if manifest_path.is_file():
        raw = manifest_path.read_bytes()
        manifest_digest = _digest(raw)
        try:
            manifest = parse_manifest(raw)
        except SidecarError as exc:
            manifest_error = str(exc)
    out: list[Candidate] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not _is_candidate(path):
            continue
        rel = path.relative_to(root).as_posix()
        if any(part.startswith(".") for part in Path(rel).parts):
            continue
        try:
            if now - path.stat().st_mtime < settle_seconds:
                continue
        except OSError:
            continue
        out.append(_candidate(root, path, rel, manifest, manifest_error, manifest_digest))
    return out


def _candidate(root: Path, path: Path, rel: str, manifest: Manifest | None, manifest_error: str | None,
               manifest_digest: str) -> Candidate:
    side = sidecar_path(path)
    siblings = [p for p in path.parent.glob(f"{_glob_escape(path.stem)}.*")
                if p != path and p.suffix.lower() in EXTS]
    side_raw = side.read_bytes() if side.is_file() else None
    in_manifest = manifest is not None and rel in manifest.rows
    digest = "-"
    try:
        if manifest_error is not None:
            digest = f"m{manifest_digest}"
            raise SidecarError(manifest_error)
        if side_raw is not None:
            digest = f"j{_digest(side_raw)}"
            if siblings:
                raise SidecarError(
                    f"metadata 側檔不合格：{side.relative_to(root).as_posix()}：同名的研報檔不只一份"
                    f"（{', '.join(sorted(p.name for p in siblings + [path]))}），無法判斷對應哪一份")
            if in_manifest:
                raise SidecarError(
                    f"metadata 側檔不合格：{rel}：同時有 {side.name} 與 {MANIFEST_NAME} 的列，不猜哪一份為準")
            meta = parse_json_bytes(side_raw, side.relative_to(root).as_posix())
        elif in_manifest:
            meta = manifest.rows[rel]  # type: ignore[union-attr]
            digest = f"c{_digest(json.dumps(meta.to_json(), sort_keys=True, ensure_ascii=False).encode())}"
        else:
            meta = None
        return Candidate(rel=rel, path=path, fingerprint=_fingerprint(path, digest), metadata=meta)
    except SidecarError as exc:
        return Candidate(rel=rel, path=path, fingerprint=_fingerprint(path, digest), sidecar_error=str(exc))


def _glob_escape(name: str) -> str:
    return "".join(f"[{c}]" if c in "*?[]" else c for c in name)


# ── 狀態檔 ──────────────────────────────────────────────────────────────


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ConnectorState:
    """一個連接器的處理紀錄：相對路徑 → {fingerprint, kind, file_hash, at}，以及上次輪詢時刻。"""

    path: Path
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_poll: float | None = None  # epoch 秒

    @classmethod
    def load(cls, path: Path) -> ConnectorState:
        """讀狀態檔；不存在＝全新。內容壞掉拋 ValueError（不當成空的：當成空的會整批重抽）。"""
        if not path.is_file():
            return cls(path=path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"連接器狀態檔無法讀取：{path}：{exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("files", {}), dict):
            raise ValueError(f"連接器狀態檔格式不對：{path}")
        last = data.get("last_poll")
        return cls(path=path, files=dict(data.get("files", {})),
                   last_poll=float(last) if isinstance(last, (int, float)) else None)

    def is_done(self, cand: Candidate) -> bool:
        entry = self.files.get(cand.rel)
        return bool(entry) and entry.get("fingerprint") == cand.fingerprint

    def record(self, cand: Candidate, kind: str, file_hash: str | None) -> None:
        self.files[cand.rel] = {"fingerprint": cand.fingerprint, "kind": kind, "file_hash": file_hash,
                                "at": _utcnow_iso()}

    def due(self, poll_minutes: int, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return self.last_poll is None or now - self.last_poll >= poll_minutes * 60 - 30

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "last_poll": self.last_poll, "files": self.files}, fh,
                          ensure_ascii=False, indent=1, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        finally:
            if tmp.exists():
                tmp.unlink()


__all__ = [
    "EXTS", "SIDECAR_INVALID", "SIDECAR_SUFFIX", "TERMINAL_KINDS", "Candidate", "ConnectorState", "FetchReport",
    "scan",
]
