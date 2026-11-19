"""交付打包：從一個 git commit 產出交給其他券商的框架原始碼 tarball，並在暫存目錄驗證交付物。

交付流程、清單的歸類規則與驗證步驟的說明在 `docs/FRAMEWORK_EXPORT.md`。

    make export                                        # HEAD → data/export/，含驗證
    uv run python scripts/export_framework.py --commit <rev> [--out <目錄>] [--license <檔>]
    uv run python scripts/export_framework.py --commit <rev> --no-verify      # 只打包
    uv run python scripts/export_framework.py --check                         # 只核對清單（零輸出檔）

**內容只來自 commit**（`git ls-tree`／`git cat-file`），不讀工作樹：未 commit 的改動不會進交付物（工作樹不乾淨時
印一行警告）。同一個 commit、同一版清單與工具產出的 tarball 逐位元組相同（成員依路徑排序、mtime 一律是 commit
時間、uid/gid 0、gzip 標頭不帶時間），日後拿 commit 重打一次比 sha256 就能對帳交出去的是哪一版。

哪些檔案進交付物、交付版怎麼改寫、驗證掃什麼，都在 `deploy/EXPORT.toml`（讀的是該 commit 裡的那一份）；
`deploy/`、`scripts/`、`ops_agent/` 的歸屬沿用 `deploy/OWNERSHIP.toml`（framework 進、tingfong_host 不進）。
每個受版控的檔案都必須恰好被一條規則歸類，否則拒絕打包（`tests/test_export_manifest.py` 在 CI 守同一件事）。
repo 根的授權聲明（`LICENSE`／`LICENSE.md`／`LICENSE.txt`）一律帶上；沒有就警告，`--license` 可指定別的檔。

交付版的改寫只有兩種，都會在對不上時拒絕打包（不做「找不到就算了」）：
- 標記：清單 `[markers].files` 列出的檔案裡，交付版刪除三種範圍。標記一律寫成該檔的註解（`#`、`//` 或 HTML 註解）：
  - 單行：行尾的註解是 `export-omit`（Markdown 表格列可寫在最後的 `|` 之前）。
  - 區塊：獨立一行的 `export-omit:begin` 到獨立一行的 `export-omit:end`（含兩個標記行）。
  - 行內片段（Markdown）：同一行裡成對的 HTML 註解 `export-omit:begin`…`export-omit:end` 之間的文字；整行只剩
    空白就整行刪。渲染後看不見，用在句子或表格儲存格的一部分。
  標記不成對、巢狀、區塊標記沒有獨立成行、或 `export-omit` 字樣不是上述形式，一律拒絕打包。
- 逐字替換：`[[rewrite]]` 每條在檔案裡必須恰好出現一次。

產出（`--out`，預設 `data/export/`；`data/` 不進版控也不進映像的 build context）：
- `<名稱>.tar.gz`：最上層目錄 `<名稱>/`，內含交付檔案、`DELIVERY_INFO.json`（來源 commit、commit 時間、清單
  雜湊、授權、檔案數）與 `SHA256SUMS`（每個交付檔的 sha256）。
- `<名稱>.tar.gz.sha256`、`<名稱>.receipt.json`（打包時間、tarball 雜湊、驗證結果；不在 tarball 裡）。

驗證（預設做，`--no-verify` 略過）在暫存目錄解開 tarball 後依序：雜湊對帳、禁用字樣掃描（`[verify].forbidden`，
例外逐檔釘行數；`[verify].advisory` 只列次數不擋）、Dockerfile 的 COPY 來源與 compose 的相對路徑掛載都在、
沒設 PROFILE_DIR 時 web 拒絕啟動、設成示範 profile 載得起來、ruff、交付版自己的 pytest（非 DB 測試；
`REPORT_MARK_DB_URL` 一律指向不存在的主機）。
`--frontend` 另在交付版裡 `npm ci`／typecheck／build／vitest，並讓 pytest 的 SPA 測試對那份 build 驗證
（`npm ci` 需要 npm registry 或本機快取）。`--keep-work` 保留驗證用的暫存目錄。

退出碼：0 成功；1 驗證失敗；2 清單、參數或 git 錯誤（沒有產出 tarball）。只用標準函式庫。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = "deploy/EXPORT.toml"
OWNERSHIP_PATH = "deploy/OWNERSHIP.toml"
OWNERSHIP_SCOPES = ("deploy/", "scripts/", "ops_agent/")
LICENSE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt")
INFO_NAME = "DELIVERY_INFO.json"
SUMS_NAME = "SHA256SUMS"
INFO_FORMAT = 1
DEFAULT_OUT = "data/export"
NAME_PREFIX = "report-mark-framework"
# 交付版的 pytest 一律連不到 DB：DB 契約測試在沒有 DB 時 skip，絕不連到任何真實資料庫。
UNREACHABLE_DB_URL = "postgresql+asyncpg://nobody:x@db.invalid:5432/none"
PYTEST_TIMEOUT = 3600

# 行尾的 `export-omit` 註解標記（`# …`、`// …`、`<!-- … -->`；Markdown 表格列可在最後的 `|` 之前）。
MARKER_RE = re.compile(r"(?:#|//|<!--)\s*export-omit(?::(begin|end))?\s*(?:-->)?\s*\|?\s*$")
# 同一行之內的片段（Markdown 的句子或表格儲存格的一部分）：`<!-- export-omit:begin -->…<!-- export-omit:end -->`。
INLINE_RE = re.compile(r"<!--\s*export-omit:begin\s*-->.*?<!--\s*export-omit:end\s*-->")
MARKER_TOKEN = "export-omit"

_EXPORT_KEYS = {"paths", "markers", "rewrite", "verify"}
_PATH_KEYS = {"include_dirs", "exclude_dirs", "include", "exclude"}


class ExportError(RuntimeError):
    """清單、git 或改寫的錯誤：不產出 tarball，退出 2。"""


# ── 清單 ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Rewrite:
    file: str
    old: str
    new: str
    reason: str


@dataclass(frozen=True)
class Manifest:
    include_dirs: tuple[str, ...]
    exclude_dirs: tuple[str, ...]
    include: frozenset[str]
    exclude: frozenset[str]
    framework: frozenset[str]
    host: frozenset[str]
    marker_files: frozenset[str]
    rewrites: tuple[Rewrite, ...]
    forbidden: tuple[str, ...]
    allow: dict[str, tuple[int, str]] = field(default_factory=dict)
    advisory: tuple[str, ...] = ()

    @property
    def forbidden_re(self) -> re.Pattern[str]:
        return re.compile("|".join(re.escape(w) for w in self.forbidden), re.IGNORECASE)


def _str_list(table: dict, key: str, where: str) -> list[str]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ExportError(f"{where}.{key} 必須是非空字串的清單")
    if len(value) != len(set(value)):
        dup = sorted({v for v in value if value.count(v) > 1})
        raise ExportError(f"{where}.{key} 有重複：{dup}")
    return value


def parse_manifest(export_text: str, ownership_text: str) -> Manifest:
    """解析並驗證 `deploy/EXPORT.toml` 與 `deploy/OWNERSHIP.toml`；任何不一致都拋 ExportError。"""
    try:
        data = tomllib.loads(export_text)
        own = tomllib.loads(ownership_text)
    except tomllib.TOMLDecodeError as exc:
        raise ExportError(f"清單不是合法 TOML：{exc}") from exc
    unknown = set(data) - _EXPORT_KEYS
    if unknown:
        raise ExportError(f"{MANIFEST_PATH} 有未知的 section：{sorted(unknown)}")
    paths = data.get("paths", {})
    if set(paths) - _PATH_KEYS:
        raise ExportError(f"[paths] 有未知的鍵：{sorted(set(paths) - _PATH_KEYS)}")
    include_dirs = _str_list(paths, "include_dirs", "[paths]")
    exclude_dirs = _str_list(paths, "exclude_dirs", "[paths]")
    include = _str_list(paths, "include", "[paths]")
    exclude = _str_list(paths, "exclude", "[paths]")
    dirs = include_dirs + exclude_dirs
    for d in dirs:
        if not d.endswith("/") or d.startswith("/") or any(ch in d for ch in "*?[]"):
            raise ExportError(f"[paths] 的目錄要以 / 結尾、相對 repo 根、不用萬用字元：{d}")
    for a in dirs:
        for b in dirs:
            if a != b and b.startswith(a):
                raise ExportError(f"[paths] 的目錄規則不可巢狀：{a} 包含 {b}")
    files = include + exclude
    both = set(include) & set(exclude)
    if both:
        raise ExportError(f"[paths] 同一個檔案同時列在 include 與 exclude：{sorted(both)}")
    for f in files:
        if f.endswith("/") or any(ch in f for ch in "*?[]"):
            raise ExportError(f"[paths] 的檔案要逐檔列、不用萬用字元：{f}")
        under = [d for d in dirs if f.startswith(d)]
        if under:
            raise ExportError(f"[paths] 的 {f} 已被目錄規則 {under[0]} 涵蓋，不要重複列")
    for p in dirs + files:
        if p.startswith(OWNERSHIP_SCOPES) or any(s.startswith(p) for s in OWNERSHIP_SCOPES):
            raise ExportError(f"{p} 屬 {OWNERSHIP_PATH} 的範圍，在那裡歸類（framework／tingfong_host）")
        if p in LICENSE_NAMES:
            raise ExportError(f"{p} 是授權聲明，一律帶上，不必列")
    try:
        framework = own["framework"]["files"]
        host = own["tingfong_host"]["files"]
    except (KeyError, TypeError) as exc:
        raise ExportError(f"{OWNERSHIP_PATH} 缺 framework／tingfong_host 的 files") from exc

    markers = data.get("markers", {})
    if set(markers) - {"files"}:
        raise ExportError(f"[markers] 有未知的鍵：{sorted(set(markers) - {'files'})}")
    marker_files = _str_list(markers, "files", "[markers]")

    rewrites = []
    raw_rewrites = data.get("rewrite", [])
    if not isinstance(raw_rewrites, list):
        raise ExportError("[[rewrite]] 必須是 array of tables")
    for i, item in enumerate(raw_rewrites):
        where = f"[[rewrite]] 第 {i + 1} 條"
        if not isinstance(item, dict) or set(item) != {"file", "old", "new", "reason"}:
            raise ExportError(f"{where} 要恰好有 file、old、new、reason 四個鍵")
        if not all(isinstance(item[k], str) and item[k] for k in item):
            raise ExportError(f"{where} 的值都要是非空字串")
        if item["old"] == item["new"]:
            raise ExportError(f"{where} 的 old 與 new 相同")
        rewrites.append(Rewrite(item["file"], item["old"], item["new"], item["reason"]))

    verify = data.get("verify", {})
    if set(verify) - {"forbidden", "allow", "advisory"}:
        raise ExportError(f"[verify] 有未知的鍵：{sorted(set(verify) - {'forbidden', 'allow', 'advisory'})}")
    forbidden = _str_list(verify, "forbidden", "[verify]")
    if not forbidden:
        raise ExportError("[verify].forbidden 不可為空")
    allow: dict[str, tuple[int, str]] = {}
    for path, entry in verify.get("allow", {}).items():
        if not isinstance(entry, dict) or set(entry) != {"lines", "reason"}:
            raise ExportError(f"[verify.allow] {path} 要恰好有 lines 與 reason")
        if type(entry["lines"]) is not int or entry["lines"] <= 0 or not str(entry["reason"]).strip():
            raise ExportError(f"[verify.allow] {path} 的 lines 要是正整數、reason 不可為空")
        allow[path] = (entry["lines"], entry["reason"])

    return Manifest(
        include_dirs=tuple(include_dirs), exclude_dirs=tuple(exclude_dirs),
        include=frozenset(include), exclude=frozenset(exclude),
        framework=frozenset(framework), host=frozenset(host),
        marker_files=frozenset(marker_files), rewrites=tuple(rewrites),
        forbidden=tuple(forbidden), allow=allow, advisory=tuple(_str_list(verify, "advisory", "[verify]")),
    )


def classify(m: Manifest, path: str) -> bool | None:
    """True＝進交付物、False＝不進、None＝沒有任何規則涵蓋（未歸類）。"""
    if path in LICENSE_NAMES:
        return True
    if path.startswith(OWNERSHIP_SCOPES):
        if path in m.framework:
            return True
        if path in m.host:
            return False
        return None
    if path in m.include:
        return True
    if path in m.exclude:
        return False
    if any(path.startswith(d) for d in m.include_dirs):
        return True
    if any(path.startswith(d) for d in m.exclude_dirs):
        return False
    return None


def manifest_problems(m: Manifest, tracked: list[str]) -> list[str]:
    """清單對 commit 的檔案清單：未歸類、列了不存在、改寫目標不在交付物裡。空清單＝沒問題。"""
    present = set(tracked)
    problems = [f"未歸類（在 {MANIFEST_PATH} 或 {OWNERSHIP_PATH} 加進 include 或 exclude）：{p}"
                for p in tracked if classify(m, p) is None]
    for p in sorted(m.include | m.exclude):
        if p not in present:
            problems.append(f"{MANIFEST_PATH} 列了不存在的檔案：{p}")
    for d in m.include_dirs + m.exclude_dirs:
        if not any(p.startswith(d) for p in present):
            problems.append(f"{MANIFEST_PATH} 的目錄規則沒有任何檔案：{d}")
    for p in sorted(m.framework | m.host):
        if p not in present:
            problems.append(f"{OWNERSHIP_PATH} 列了不存在的檔案：{p}")
    for p in sorted(m.marker_files | {r.file for r in m.rewrites} | set(m.allow)):
        if p not in present or not classify(m, p):
            problems.append(f"{MANIFEST_PATH} 的標記、改寫或白名單指向不進交付物（或不存在）的檔案：{p}")
    return problems


# ── 改寫 ─────────────────────────────────────────────────────────────────


def strip_markers(text: str, path: str) -> tuple[str, int]:
    """刪掉標記行與標記區塊；回傳 (新內容, 刪掉的行數)。標記不成對、巢狀、或整檔沒有標記都拋 ExportError。"""
    out: list[str] = []
    removed = 0
    open_at: int | None = None
    seen = False
    for no, line in enumerate(text.splitlines(keepends=True), 1):
        if open_at is None and INLINE_RE.search(line):
            seen = True
            stripped = INLINE_RE.sub("", line)
            if not stripped.strip():
                removed += 1
                continue
            line = stripped
        mk = MARKER_RE.search(line.rstrip("\r\n"))
        if mk:
            seen = True
            kind = mk.group(1)
            if kind and not line.lstrip().startswith(("#", "//", "<!--")):
                raise ExportError(f"{path}:{no}：export-omit:{kind} 要獨立一行（同一行的片段用成對的 HTML 註解標記）")
            if kind == "begin":
                if open_at is not None:
                    raise ExportError(f"{path}:{no}：export-omit:begin 巢狀（第 {open_at} 行的區塊還沒結束）")
                open_at = no
            elif kind == "end":
                if open_at is None:
                    raise ExportError(f"{path}:{no}：export-omit:end 沒有對應的 begin")
                open_at = None
            removed += 1
            continue
        if MARKER_TOKEN in line and open_at is None:
            raise ExportError(f"{path}:{no}：有 {MARKER_TOKEN} 字樣但不是行尾註解標記（標記要寫在該行最後）")
        if open_at is not None:
            removed += 1
            continue
        out.append(line)
    if open_at is not None:
        raise ExportError(f"{path}:{open_at}：export-omit:begin 沒有對應的 end")
    if not seen:
        raise ExportError(f"{path} 列在 [markers].files 卻沒有任何標記（拿掉這條，或補上標記）")
    return "".join(out), removed


def has_marker(text: str) -> bool:
    return any(MARKER_RE.search(line) or INLINE_RE.search(line) for line in text.splitlines())


def apply_rewrites(text: str, path: str, rewrites: list[Rewrite]) -> str:
    for rw in rewrites:
        count = text.count(rw.old)
        if count != 1:
            raise ExportError(f"{path}：[[rewrite]] 的 old 應恰好出現 1 次，實際 {count} 次：{rw.old!r}")
        text = text.replace(rw.old, rw.new)
    return text


def transform(m: Manifest, path: str, data: bytes) -> tuple[bytes, bool]:
    """交付版的內容；回傳 (內容, 是否改寫過)。未列在標記清單的檔案出現標記也拋錯（漏列＝標記會原樣出貨）。"""
    rewrites = [rw for rw in m.rewrites if rw.file == path]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        if path in m.marker_files or rewrites:
            raise ExportError(f"{path} 不是 UTF-8 文字檔，不能套標記或改寫") from None
        return data, False
    changed = False
    if path in m.marker_files:
        text, _ = strip_markers(text, path)
        changed = True
    elif has_marker(text):
        raise ExportError(f"{path} 有 export-omit 標記但不在 [markers].files：加進清單才會生效")
    if rewrites:
        text = apply_rewrites(text, path, rewrites)
        changed = True
    return (text.encode("utf-8"), True) if changed else (data, False)


# ── git ──────────────────────────────────────────────────────────────────


def _git(*args: str, repo: Path = REPO_ROOT, binary: bool = False) -> str | bytes:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if proc.returncode != 0:
        raise ExportError(f"git {' '.join(args)} 失敗：{proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout if binary else proc.stdout.decode("utf-8")


@dataclass(frozen=True)
class Entry:
    path: str
    mode: str
    sha: str


@dataclass(frozen=True)
class Commit:
    sha: str
    tree: str
    date_iso: str
    timestamp: int
    entries: dict[str, Entry]


def resolve_commit(rev: str, repo: Path = REPO_ROOT) -> Commit:
    sha = str(_git("rev-parse", "--verify", f"{rev}^{{commit}}", repo=repo)).strip()
    meta = str(_git("show", "-s", "--format=%T%n%cI%n%ct", sha, repo=repo)).split("\n")
    entries: dict[str, Entry] = {}
    raw = bytes(_git("ls-tree", "-r", "-z", "--full-tree", sha, repo=repo, binary=True))
    for item in raw.split(b"\0"):
        if not item:
            continue
        head, path = item.decode("utf-8").split("\t", 1)
        mode, kind, obj = head.split(" ")
        if kind != "blob":
            raise ExportError(f"{path} 是 {kind}（子模組不支援）")
        entries[path] = Entry(path, mode, obj)
    return Commit(sha=sha, tree=meta[0], date_iso=meta[1], timestamp=int(meta[2]), entries=entries)


class BlobReader:
    """`git cat-file --batch`：一個行程讀完所有 blob。"""

    def __init__(self, repo: Path = REPO_ROOT):
        self.proc = subprocess.Popen(["git", "-C", str(repo), "cat-file", "--batch"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def read(self, sha: str) -> bytes:
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(sha.encode() + b"\n")
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().decode().split()
        if len(header) != 3 or header[1] != "blob":
            raise ExportError(f"git cat-file 讀不到 {sha}：{header}")
        data = self.proc.stdout.read(int(header[2]))
        self.proc.stdout.read(1)
        return data

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.wait()


# ── 打包 ─────────────────────────────────────────────────────────────────


@dataclass
class Build:
    name: str
    files: dict[str, tuple[bytes, int]]        # 交付路徑 → (內容, 檔案權限)
    transformed: list[str]
    excluded: list[str]
    license_source: str | None
    info: dict


def build(commit: Commit, license_file: Path | None = None, repo: Path = REPO_ROOT) -> Build:
    reader = BlobReader(repo)
    try:
        def blob(path: str) -> bytes:
            entry = commit.entries.get(path)
            if entry is None:
                raise ExportError(f"commit {commit.sha[:12]} 沒有 {path}（早於交付清單的 commit 不能用本工具打包）")
            return reader.read(entry.sha)

        export_text = blob(MANIFEST_PATH)
        m = parse_manifest(export_text.decode("utf-8"), blob(OWNERSHIP_PATH).decode("utf-8"))
        tracked = sorted(commit.entries)
        problems = manifest_problems(m, tracked)
        if problems:
            raise ExportError("清單與 commit 對不上：\n  " + "\n  ".join(problems))
        files: dict[str, tuple[bytes, int]] = {}
        transformed: list[str] = []
        excluded: list[str] = []
        for path in tracked:
            if not classify(m, path):
                excluded.append(path)
                continue
            entry = commit.entries[path]
            data = reader.read(entry.sha)
            if entry.mode == "120000":
                raise ExportError(f"{path} 是 symlink：交付物不帶 symlink（改成一般檔案，或排除）")
            data, changed = transform(m, path, data)
            if changed:
                transformed.append(path)
            files[path] = (data, 0o755 if entry.mode == "100755" else 0o644)
    finally:
        reader.close()

    license_source = next((p for p in LICENSE_NAMES if p in files), None)
    if license_file is not None:
        for p in LICENSE_NAMES:
            files.pop(p, None)
        files["LICENSE"] = (license_file.read_bytes(), 0o644)
        license_source = f"--license {license_file.name}"

    short = commit.sha[:12]
    day = datetime.fromtimestamp(commit.timestamp, tz=timezone.utc).strftime("%Y%m%d")
    name = f"{NAME_PREFIX}-{day}-{short}"
    info = {
        "format": INFO_FORMAT,
        "name": name,
        "source_commit": commit.sha,
        "source_tree": commit.tree,
        "source_commit_date": commit.date_iso,
        "manifest_sha256": hashlib.sha256(export_text).hexdigest(),
        "license": license_source,
        "file_count": len(files) + 2,
        "excluded_count": len(excluded),
        "transformed": sorted(transformed),
    }
    return Build(name=name, files=files, transformed=transformed, excluded=excluded,
                 license_source=license_source, info=info)


def sha256sums(files: dict[str, tuple[bytes, int]]) -> bytes:
    lines = [f"{hashlib.sha256(data).hexdigest()}  {path}\n" for path, (data, _) in sorted(files.items())]
    return "".join(lines).encode("utf-8")


def write_tarball(b: Build, timestamp: int, out: Path) -> Path:
    files = dict(b.files)
    files[INFO_NAME] = ((json.dumps(b.info, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), 0o644)
    files[SUMS_NAME] = (sha256sums(files), 0o644)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{b.name}.tar.gz"
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        dirs: set[str] = set()
        for path in sorted(files):
            parents = PurePosixPath(path).parents
            for parent in sorted((p.as_posix() for p in parents if p.as_posix() != "."), key=len):
                if parent not in dirs:
                    dirs.add(parent)
                    ti = _tarinfo(f"{b.name}/{parent}", timestamp, tarfile.DIRTYPE, 0o755)
                    tar.addfile(ti)
            data, mode = files[path]
            ti = _tarinfo(f"{b.name}/{path}", timestamp, tarfile.REGTYPE, mode)
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
    with open(target, "wb") as fh, gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0) as gz:
        gz.write(raw.getvalue())
    return target


def _tarinfo(name: str, timestamp: int, kind: bytes, mode: int) -> tarfile.TarInfo:
    ti = tarfile.TarInfo(name)
    ti.type = kind
    ti.mode = mode
    ti.mtime = timestamp
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    return ti


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── 驗證 ─────────────────────────────────────────────────────────────────


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""


def scan_forbidden(root: Path, m: Manifest) -> list[str]:
    """交付物裡的禁用字樣（含路徑本身）；白名單逐檔釘行數。回傳問題清單。"""
    pat = m.forbidden_re
    byte_pats = [w.encode("utf-8").lower() for w in m.forbidden]
    problems = []
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = f.relative_to(root).as_posix()
        if pat.search(rel):
            problems.append(f"路徑含禁用字樣：{rel}")
        if rel in LICENSE_NAMES:
            continue                       # 授權聲明必須寫出著作權人
        data = f.read_bytes()
        try:
            lines = [f"{rel}:{i}: {ln.strip()[:120]}" for i, ln in enumerate(data.decode("utf-8").splitlines(), 1)
                     if pat.search(ln)]
        except UnicodeDecodeError:
            low = data.lower()
            lines = [f"{rel}: 二進位檔含 {w.decode('utf-8')!r}" for w in byte_pats if w in low]
        allowed = m.allow.get(rel, (0, ""))[0]
        if len(lines) != allowed:
            problems.append(f"{rel}（允許 {allowed} 行、實際 {len(lines)} 行）\n      " + "\n      ".join(lines))
    for rel in m.allow:
        if not (root / rel).is_file():
            problems.append(f"[verify.allow] 的 {rel} 不在交付物裡")
    return problems


def scan_advisory(root: Path, m: Manifest) -> list[str]:
    """`[verify].advisory` 字樣的出現次數（逐檔一行，區分大小寫）：不擋打包，留給交付前人工判斷。"""
    if not m.advisory:
        return []
    pat = re.compile("|".join(re.escape(w) for w in m.advisory))
    out = []
    for f in sorted(p for p in root.rglob("*") if p.is_file() and p.name != SUMS_NAME):
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        n = sum(1 for ln in text.splitlines() if pat.search(ln))
        if n:
            out.append(f"{f.relative_to(root).as_posix()}：{n} 行")
    return out


_COPY_RE = re.compile(r"^\s*(?:COPY|ADD)\s+(.*)$", re.IGNORECASE)


def dockerfile_sources(text: str) -> list[str]:
    """Dockerfile 從 build context 取的來源路徑（略過 `--from=` 與 URL）。"""
    sources: list[str] = []
    for line in text.splitlines():
        mk = _COPY_RE.match(line)
        if not mk:
            continue
        args = mk.group(1).split()
        if any(a.startswith("--from") for a in args):
            continue
        args = [a for a in args if not a.startswith("--")]
        for src in args[:-1]:
            if not src.startswith(("http://", "https://")):
                sources.append(src)
    return sources


_COMPOSE_MOUNT_RE = re.compile(r"^\s*-\s*[\"']?(\.\.?/[^:\s\"']+):")


def compose_relative_mounts(text: str) -> list[str]:
    """compose 檔裡以相對路徑掛載的主機路徑（`- ../x:/y`）；變數展開的路徑不在範圍內。"""
    return [mk.group(1) for line in text.splitlines() if (mk := _COMPOSE_MOUNT_RE.match(line))]


def check_compose_mounts(root: Path) -> list[str]:
    problems = []
    for f in sorted(root.rglob("docker-compose*.yml")):
        for src in compose_relative_mounts(f.read_text(encoding="utf-8")):
            if not (f.parent / src).exists():
                problems.append(f"{f.relative_to(root)}：掛載的 {src} 不在交付物裡")
    return problems


def check_dockerfiles(root: Path) -> list[str]:
    problems = []
    for df in sorted(root.rglob("Dockerfile")):
        for src in dockerfile_sources(df.read_text(encoding="utf-8")):
            if src == ".":
                continue
            if not (root / src.rstrip("/")).exists():
                problems.append(f"{df.relative_to(root)}：COPY 的來源 {src} 不在交付物裡")
    return problems


def _run(cmd: list[str], cwd: Path, env: dict[str, str], timeout: int = PYTEST_TIMEOUT) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return 124, f"逾時（{timeout} 秒）"
    except OSError as exc:
        return 127, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr)


def _tail(text: str, n: int = 40) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def delivery_env(root: Path, *, spa_built: bool) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("REPORT_MARK_", "PROFILE_DIR", "VIRTUAL_ENV", "PYTHONPATH"))}
    env.update({
        "REPORT_MARK_DB_URL": UNREACHABLE_DB_URL,
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    if not spa_built:
        env["SKIP_SPA_TESTS"] = "1"
    else:
        env.pop("SKIP_SPA_TESTS", None)
    return env


def verify(tarball: Path, m: Manifest, *, frontend: bool, work: Path, python: str = sys.executable) -> list[Step]:
    steps: list[Step] = []
    with tarfile.open(tarball, "r:gz") as tar:
        tar.extractall(work, filter="data")
    tops = [p for p in work.iterdir()]
    if len(tops) != 1 or not tops[0].is_dir():
        return [Step("解開", False, f"tarball 最上層應恰好一個目錄，實際：{[p.name for p in tops]}")]
    root = tops[0]

    bad = []
    for line in (root / SUMS_NAME).read_text(encoding="utf-8").splitlines():
        digest, rel = line.split("  ", 1)
        if file_sha256(root / rel) != digest:
            bad.append(rel)
    listed = {ln.split("  ", 1)[1] for ln in (root / SUMS_NAME).read_text(encoding="utf-8").splitlines()}
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} - {SUMS_NAME}
    bad += sorted(actual ^ listed)
    steps.append(Step("雜湊對帳", not bad, "、".join(bad[:20])))

    leaks = scan_forbidden(root, m)
    hints = scan_advisory(root, m)
    steps.append(Step("禁用字樣掃描", not leaks, "\n    ".join(leaks[:40])))
    if m.advisory:
        steps.append(Step("提示字樣（不擋）", True, "\n    ".join(hints) if hints else "無"))

    docker = check_dockerfiles(root) + check_compose_mounts(root)
    steps.append(Step("Dockerfile 來源與 compose 掛載", not docker, "\n    ".join(docker)))

    spa_built = False
    if frontend:
        ok, detail = verify_frontend(root)
        steps.append(Step("前端（npm ci／typecheck／build／vitest）", ok, detail))
        spa_built = ok
    env = delivery_env(root, spa_built=spa_built)

    # 沒設 PROFILE_DIR：交付版沒有預設 profile，web 的 lifespan 一啟動就拒絕（訊息要說出變數名）。
    rc, out = _run([python, "-c", "from fastapi.testclient import TestClient; from web.server import app\n"
                    "with TestClient(app):\n    pass"], root, env, timeout=600)
    refused = rc != 0 and "PROFILE_DIR" in out and "ProfileError" in out
    steps.append(Step("沒設 PROFILE_DIR 時 web 拒絕啟動", refused, _tail(out, 3) if refused else _tail(out, 15)))
    # 設了示範 profile：載得起來、web import 得了。
    demo_env = {**env, "PROFILE_DIR": "profiles/demo"}
    rc, out = _run([python, "-c", "from app.profile import get_profile; import web.server; "
                    "print(get_profile().root.name)"], root, demo_env, timeout=600)
    steps.append(Step("PROFILE_DIR=profiles/demo 載入與 web 匯入", rc == 0, _tail(out, 15)))

    rc, out = _run([python, "-m", "ruff", "check", "--no-cache", "."], root, env, timeout=600)
    steps.append(Step("ruff", rc == 0, _tail(out, 20)))

    rc, out = _run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--ignore-glob=tests/*_db.py"],
                   root, env)
    summary = next((ln for ln in reversed(out.strip().splitlines()) if " passed" in ln or " failed" in ln
                    or " error" in ln), "")
    steps.append(Step("pytest（非 DB）", rc == 0, summary if rc == 0 else _tail(out, 60)))
    return steps


def verify_frontend(root: Path) -> tuple[bool, str]:
    npm = shutil.which("npm")
    if npm is None:
        return False, "找不到 npm"
    fe = root / "frontend"
    env = {k: v for k, v in os.environ.items() if not k.startswith("REPORT_MARK_")}
    for cmd in (["ci", "--no-audit", "--no-fund", "--prefer-offline"], ["run", "typecheck"], ["run", "build"],
                ["test"]):
        rc, out = _run([npm, *cmd], fe, env, timeout=1800)
        if rc != 0:
            return False, f"npm {' '.join(cmd)} 失敗（rc={rc}）\n" + _tail(out, 40)
    return True, ""


# ── CLI ──────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="從 git commit 產出框架交付物（見模組 docstring）")
    ap.add_argument("--commit", default="HEAD", help="要交付的 commit（預設 HEAD）")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"輸出目錄（預設 {DEFAULT_OUT}，相對 repo 根）")
    ap.add_argument("--license", type=Path, help="改用這份授權聲明（交付物裡命名為 LICENSE）")
    ap.add_argument("--no-verify", action="store_true", help="只打包，不驗證")
    ap.add_argument("--frontend", action="store_true",
                    help="驗證時另在交付版建置並測試前端（npm ci 需要 registry 或快取）")
    ap.add_argument("--keep-work", action="store_true", help="保留驗證用的暫存目錄（印出路徑）")
    ap.add_argument("--check", action="store_true", help="只核對清單與 commit（不打包、不寫檔）")
    args = ap.parse_args(argv)

    try:
        if args.license is not None and not args.license.is_file():
            raise ExportError(f"--license 指定的檔案不存在：{args.license}")
        commit = resolve_commit(args.commit)
        if args.check:
            reader = BlobReader()
            try:
                m = parse_manifest(reader.read(commit.entries[MANIFEST_PATH].sha).decode("utf-8"),
                                   reader.read(commit.entries[OWNERSHIP_PATH].sha).decode("utf-8"))
            finally:
                reader.close()
            problems = manifest_problems(m, sorted(commit.entries))
            for p in problems:
                print(p)
            print(f"{commit.sha[:12]}：{'清單有問題' if problems else '清單與 commit 一致'}")
            return 2 if problems else 0
        head = str(_git("rev-parse", "HEAD")).strip()
        if commit.sha == head and str(_git("status", "--porcelain")).strip():
            print("警告：工作樹有未 commit 的改動；交付物只含 commit 的內容，這些改動不會進去。", file=sys.stderr)
        b = build(commit, args.license)
    except ExportError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 2

    out = Path(args.out)
    out = out if out.is_absolute() else REPO_ROOT / out
    tarball = write_tarball(b, commit.timestamp, out)
    digest = file_sha256(tarball)
    (out / f"{tarball.name}.sha256").write_text(f"{digest}  {tarball.name}\n", encoding="utf-8")
    print(f"交付物：{tarball}")
    print(f"  commit {commit.sha}（{commit.date_iso}）")
    print(f"  {len(b.files)} 個檔案進交付物、{len(b.excluded)} 個排除、{len(b.transformed)} 個改寫；sha256 {digest}")
    warnings = []
    if b.license_source is None:
        warnings.append("交付物沒有授權聲明：repo 根沒有 LICENSE，也沒有給 --license")
    for w in warnings:
        print(f"警告：{w}", file=sys.stderr)

    steps: list[Step] = []
    if not args.no_verify:
        m = parse_manifest(_blob_text(commit, MANIFEST_PATH), _blob_text(commit, OWNERSHIP_PATH))
        work = Path(tempfile.mkdtemp(prefix="report-mark-export-"))
        try:
            steps = verify(tarball, m, frontend=args.frontend, work=work)
        finally:
            if args.keep_work:
                print(f"驗證用的暫存目錄：{work}")
            else:
                shutil.rmtree(work, ignore_errors=True)
        for s in steps:
            print(f"  [{'通過' if s.ok else '失敗'}] {s.name}" + (f"：{s.detail}" if s.detail and s.ok else ""))
            if not s.ok and s.detail:
                print("    " + s.detail.replace("\n", "\n    "))

    receipt = {
        **b.info,
        "tarball": tarball.name,
        "tarball_sha256": digest,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "warnings": warnings,
        "verification": None if args.no_verify else [
            {"step": s.name, "ok": s.ok, "detail": s.detail[-4000:]} for s in steps],
    }
    receipt_path = out / f"{b.name}.receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"收據：{receipt_path}")
    if steps and not all(s.ok for s in steps):
        print("驗證失敗：交付物已產出但不要交出去。", file=sys.stderr)
        return 1
    return 0


def _blob_text(commit: Commit, path: str) -> str:
    reader = BlobReader()
    try:
        return reader.read(commit.entries[path].sha).decode("utf-8")
    finally:
        reader.close()


if __name__ == "__main__":
    sys.exit(main())
