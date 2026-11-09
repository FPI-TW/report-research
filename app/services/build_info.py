"""建置資訊：正在跑的程式是哪一個 commit、哪一個版本 tag、建置時工作樹是否乾淨。

給兩個讀者：`GET /api/admin/diagnostics` 的 `versions.build`（管理員對帳「部署的是不是我以為的那一版」）與
`GET /metrics` 的 `report_mark_build_info`（券商自己的監控對帳多台、多次部署）。**唯讀、不擋任何操作**：
讀不到就說讀不到，不拒起、不告警。

## 來源（`collect` 的判定順序）

1. **建置資訊檔** `build_info.json`（repo 根，或 `BUILD_INFO_FILE` 指定）。由 `scripts/build_info.py write` 寫：
   - 容器映像：`deploy/container/Dockerfile` 在 build 時以 build arg（`BUILD_COMMIT`、`BUILD_VERSION`、
     `BUILD_TREE_CLEAN`）寫進映像，`source=image`。映像不帶 `.git`（`.dockerignore`），所以這是唯一來源；
     build 時沒給 arg 就是 null（「建置時未提供」），不猜。
   - 主機：`make build-info` 在部署目錄跑 git（`rev-parse`、`describe --tags`、`status --porcelain`）寫入，
     `source=host`。web 行程本身不跑 git（理由同 `app/services/diagnostics.py`「版本資訊怎麼取得」）。
2. 主機的檔案若與**行程啟動時** `.git` 讀到的 commit 不同（更新了程式卻沒重跑 `make build-info`），檔案作廢：
   commit 改用 `.git` 的、版本與乾淨與否回 null，`stale_file=true`。不讓過期的檔把舊版本號掛在新程式上。
3. 沒有檔案：只有 `.git` 的 commit（`source=git`），版本與乾淨與否 null。
4. 都沒有：`available=false`。

讀一次就快取（行程內不變；程式更新要重啟 web 才生效，與 `diagnostics.GIT_AT_START` 同一個語意）。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_INFO_ENV = "BUILD_INFO_FILE"
DEFAULT_PATH = REPO_ROOT / "build_info.json"
FILE_SOURCES = ("image", "host")
SOURCES = (*FILE_SOURCES, "git")
_MAX_BYTES = 16 * 1024
_SHA_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
# 版本字串原樣交給 Prometheus label 與管理頁：限長、限字元，免得檔案內容決定輸出。
_VERSION_RE = re.compile(r"^[A-Za-z0-9._+\-/]{1,128}$")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T[0-9:.]+(Z|[+-]\d{2}:\d{2})?$")

_CACHE: dict[str, Any] | None = None


def info_path() -> Path:
    return Path(os.environ.get(BUILD_INFO_ENV) or DEFAULT_PATH)


def parse(raw: Any) -> dict | None:
    """建置資訊檔的內容 → 正規化的 dict；形狀不對回 None（整份作廢，不挑著用）。"""
    if not isinstance(raw, dict) or raw.get("source") not in FILE_SOURCES:
        return None
    commit, version, clean, built_at = raw.get("commit"), raw.get("version"), raw.get("tree_clean"), raw.get("built_at")
    if commit is not None and not (isinstance(commit, str) and _SHA_RE.match(commit)):
        return None
    if version is not None and not (isinstance(version, str) and _VERSION_RE.match(version)):
        return None
    if clean is not None and not isinstance(clean, bool):
        return None
    if built_at is not None and not (isinstance(built_at, str) and _ISO_RE.match(built_at)):
        return None
    return {"source": raw["source"], "commit": commit, "version": version, "tree_clean": clean, "built_at": built_at}


def read_file(path: Path | None = None) -> dict | None:
    path = path or info_path()
    try:
        with open(path, "rb") as fh:
            data = fh.read(_MAX_BYTES + 1)
    except OSError:
        return None
    if len(data) > _MAX_BYTES:
        return None
    try:
        return parse(json.loads(data.decode("utf-8")))
    except (UnicodeDecodeError, ValueError):
        return None


def resolve(file_info: dict | None, git_commit: str | None) -> dict:
    """判定（模組 docstring「來源」）。純函式。"""
    empty = {"available": False, "source": None, "commit": None, "version": None, "tree_clean": None,
             "built_at": None, "stale_file": False}
    if file_info is not None:
        if file_info["source"] == "host" and git_commit and file_info["commit"] != git_commit:
            return {**empty, "available": True, "source": "git", "commit": git_commit, "stale_file": True}
        return {**empty, **file_info, "available": True}
    if git_commit:
        return {**empty, "available": True, "source": "git", "commit": git_commit}
    return empty


def collect(git_commit: str | None) -> dict:
    """行程內快取的建置資訊。`git_commit`＝行程啟動時 `.git` 讀到的 commit（`diagnostics.GIT_AT_START`）。"""
    global _CACHE
    if _CACHE is None:
        _CACHE = resolve(read_file(), git_commit)
    return dict(_CACHE)


def _reset_for_tests() -> None:
    global _CACHE
    _CACHE = None
