# tests/test_docs_contract.py
"""文件對現況的兩條機械式守門：檔案路徑要存在、HTTP 端點要被記載。

════════════════════════════════════════════════════════════════════════
為什麼需要這兩支
════════════════════════════════════════════════════════════════════════
2026-07-29 的全 repo 文件稽核找出 85 條漂移，事後歸納出三種型態，其中兩種
是**純機械性**的、根本不需要人來抓：

  1. 改名／刪檔之後文件沒跟上。`intent.py` 改名為 `scope_router.py` 之後，
     CLAUDE.md ×2 與 README ×3 仍指著它，撐了好幾個里程碑沒人發現——因為
     沒有任何東西會因此變紅。
  2. 新端點上線但沒進 API 表。那次稽核時 README 與 WORKFLOW 合計漏了 10+
     條端點，包含**唯一免認證的 `/healthz`** 與四支雷達 API；而 `{id}` vs
     `{report_id}`、`{code}` vs `{code:path}` 這種參數名漂移，照抄就是 404
     或 422。

第三種型態（把「規則」寫成「現況」，例如「Config 已集中」）需要判斷，測試
擋不住，只能靠稽核——所以這裡不假裝能涵蓋它。

**刻意不做的第三支**：曾考慮加「三份文件裡的測試檔數必須一致且等於實際檔
數」。已否決：以 `find` 為基準會被他人未追蹤的 WIP 測試檔弄紅（實測磁碟 97
vs 已追蹤 94），以 `git ls-files` 為基準又擋不住「加了測試但忘記 git add」。
正解是**把數字從文件裡刪掉**，那已經做了。

════════════════════════════════════════════════════════════════════════
掃描範圍為什麼只有五份
════════════════════════════════════════════════════════════════════════
只涵蓋「對現況做斷言」的文件。設計稿、實作計畫與架構檢視報告會**刻意指名
不存在的檔案**——前者是提案（`app/errors.py`、`db/migrations/`），後者是
反例（「`intent.py` 檔案不存在」「`web/static/app/` 已刪除」）。把它們納入
等於保證永遠紅，而一支永遠紅的測試會在兩週內被 skip 掉。
"""
from __future__ import annotations

import ast
import fnmatch
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 對現況做斷言的文件。新增這類文件時要一併加進來。
LIVING_DOCS = (
    "CLAUDE.md",
    "AGENTS.md",
    "README.md",
    "docs/WORKFLOW.md",
    "docs/ARCHITECTURE.md",
    "docs/EXTRACTION.md",
)

# 走訪檔案系統時整棵剪掉的目錄（產物、相依、快取、唯讀來源）。
_PRUNE = {
    ".git", "node_modules", ".venv", "__pycache__", "dist", "data",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".superpowers",
    ".playwright-cli", ".playwright-mcp", "研報自動匯入",
}

_BACKTICK = re.compile(r"`([^`\n]+)`")
_LINE_SUFFIX = re.compile(r":\d+(?:-\d+)?$")       # `foo.py:12` / `foo.py:12-34`

_PATHY_EXTS = (
    ".py", ".md", ".ts", ".tsx", ".js", ".mjs", ".css", ".sql", ".typ", ".json",
    ".yml", ".yaml", ".toml", ".sh", ".html", ".service", ".timer", ".conf", ".lock",
)
_TOP_DIRS = (
    "app/", "web/", "scripts/", "tests/", "db/", "eval/", "deploy/",
    "frontend/", "docs/", ".github/",
)

# 任何文件都可以合法提到、但樹裡不會有的路徑：建置產物與執行期產物。
# `frontend/dist` 刻意留在 _PRUNE 裡而不是靠實際存在與否判斷——否則這支測試
# 會變成「本機建過就綠、CI 沒建就紅」的環境相依測試，本專案已經被這種東西
# 咬過一次（研報 PDF 內容測試依賴 CJK 字型）。
_GLOBAL_ALLOW = {
    "frontend/dist",
    "frontend/dist/index.html",
    "worklist_batch*.json",          # data/ 底下的執行期分批檔
}

# **刻意指名不存在的東西**——只在指定文件裡放行。範圍收到單一文件是重點：
# 例如 CLAUDE.md 拿 `app/api/` 說明「這個 repo 不是 FinDB」是正確敘述，但同一個
# 字串出現在 README 就是必須抓到的陳舊引用。全域放行等於自廢武功。
_DOC_ALLOW = {
    "CLAUDE.md": {
        "app/api/",              # 用來說明「這個 repo 不是 FinDB」，正因不存在才要寫
    },
    "README.md": {
        "Project/CLAUDE.md",     # repo 之外的上層專案目錄
    },
}


def _iter_repo_files() -> set[str]:
    """repo 內所有「真實存在且非產物」的檔案，相對 repo root 的 POSIX 路徑。"""
    out: set[str] = set()
    stack = [REPO_ROOT]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.name in _PRUNE:
                continue
            if e.is_dir():
                stack.append(e)
            else:
                out.add(e.relative_to(REPO_ROOT).as_posix())
    return out


_REPO_FILES = _iter_repo_files()
_REPO_DIRS = {p.rsplit("/", 1)[0] for p in _REPO_FILES if "/" in p}
_REPO_DIRS |= {d.rsplit("/", 1)[0] for d in list(_REPO_DIRS) if "/" in d}


def _path_candidate(raw: str) -> str | None:
    """把一段反引號內容判成「應該存在的路徑」，或 None 表示不是路徑。

    保守優先：寧可放過幾個真路徑，也不要製造假紅——假紅會讓整支測試被停用。
    """
    s = _LINE_SUFFIX.sub("", raw.strip())
    if not s or " " in s:
        return None                                   # 指令列，不是路徑
    if any(c in s for c in "<>{}()|$=,"):
        return None                                   # 佔位符／程式碼片段／查詢字串
    if s.startswith(("data/", "../", "/", "http", "~")):
        return None                                   # 產物／repo 外／URL 路徑／絕對路徑
    if "." not in s and "/" not in s:
        return None                                   # 裸識別字
    name = s.rstrip("/").rsplit("/", 1)[-1]
    if not name or name.startswith("."):
        return None                                   # `.typ`、`.service` 這種純副檔名片段
    if not (s.startswith(_TOP_DIRS) or s.endswith(_PATHY_EXTS)):
        return None
    return s


def _resolves(s: str) -> bool:
    """存在性判定。支援三種寫法，因為三種在本專案都是慣用的。"""
    bare = s.rstrip("/")
    if any(ch in bare for ch in "*?"):                # glob：`app/templates/*.typ`
        return any(fnmatch.fnmatch(f, bare) or fnmatch.fnmatch(f.rsplit("/", 1)[-1], bare)
                   for f in _REPO_FILES)
    if bare in _REPO_FILES or bare in _REPO_DIRS:     # 完整路徑
        return True
    suffix = "/" + bare                               # 裸檔名或部分路徑：`answer.py`、`routers/ask.py`
    return any(f.endswith(suffix) for f in _REPO_FILES) or any(
        d.endswith(suffix) for d in _REPO_DIRS
    )


class DocPathsExist(unittest.TestCase):
    """文件裡反引號包住、形似路徑的字串，都必須指得到真實檔案。"""

    def test_referenced_paths_exist(self):
        broken: list[str] = []
        for doc in LIVING_DOCS:
            text = (REPO_ROOT / doc).read_text(encoding="utf-8")
            allowed = _GLOBAL_ALLOW | _DOC_ALLOW.get(doc, set())
            for m in _BACKTICK.finditer(text):
                cand = _path_candidate(m.group(1))
                if cand is None or cand in allowed or cand.rstrip("/") in allowed:
                    continue
                if not _resolves(cand):
                    line = text.count("\n", 0, m.start()) + 1
                    broken.append(f"{doc}:{line}  `{m.group(1)}`")
        self.assertEqual(
            [], broken,
            "文件指向不存在的檔案。改名或刪檔時要同步改文件；若是刻意指名不存在的"
            "東西（提案、反例），加進 _PATH_ALLOWLIST 並寫明理由：\n  "
            + "\n  ".join(broken),
        )

    def test_scan_actually_covers_something(self):
        """反轉守門：規則若被改到什麼都不匹配，上面那條會永遠綠。

        沒有這條的話，把 `_path_candidate` 改成 `return None` 就能讓守門靜默失效。
        """
        n = sum(
            1
            for doc in LIVING_DOCS
            for m in _BACKTICK.finditer((REPO_ROOT / doc).read_text(encoding="utf-8"))
            if _path_candidate(m.group(1)) is not None
        )
        self.assertGreater(n, 300, f"路徑候選只剩 {n} 條，判定規則八成被改窄了")


# ───────────────────────── 端點對帳 ─────────────────────────

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}
_ROUTER_DIR = REPO_ROOT / "web" / "routers"

# 記載端點的文件。任一份寫到就算數（README 是總表，WORKFLOW 是契約細節）。
_API_DOCS = ("README.md", "docs/WORKFLOW.md")


def _declared_routes() -> list[tuple[str, str, str, int]]:
    """靜態抓出 `web/routers/*.py` 的所有路由：(method, path, file, lineno)。

    刻意用 AST 而不是 import `web.server`：後者會拉起 config／DB／embed 整條
    相依鏈（在 WSL 要 4-8 秒，且需要 auth 環境變數），而我們只要那個字串常值。
    同樣的理由見 `tests/test_env_loading.py` 的接線層守門。
    """
    routes: list[tuple[str, str, str, int]] = []
    for f in sorted(_ROUTER_DIR.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                fn = dec.func
                if not (isinstance(fn, ast.Attribute) and fn.attr in _HTTP_METHODS):
                    continue
                if not (isinstance(fn.value, ast.Name) and fn.value.id == "router"):
                    continue
                arg = dec.args[0] if dec.args else None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    routes.append((fn.attr.upper(), arg.value, f.name, node.lineno))
    return routes


class ApiSurfaceIsDocumented(unittest.TestCase):
    """程式碼上的路由與文件 API 表必須雙向對得起來。"""

    def test_routers_declare_no_prefix(self):
        """`APIRouter(prefix=...)` 會讓 AST 抓到的路徑不完整，這支守門就會說謊。

        目前所有 router 都是 `APIRouter()`、路徑寫全。哪天有人加了 prefix，
        要先更新這支測試的組路徑邏輯，而不是讓它靜默比對半截路徑。
        """
        offenders = []
        for f in sorted(_ROUTER_DIR.glob("*.py")):
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "APIRouter"
                    and any(kw.arg == "prefix" for kw in node.keywords)
                ):
                    offenders.append(f"{f.name}:{node.lineno}")
        self.assertEqual([], offenders, f"router 帶了 prefix，需先更新本測試：{offenders}")

    def test_every_route_is_documented(self):
        routes = _declared_routes()
        self.assertGreater(len(routes), 30, "抓到的路由太少，AST 規則八成失效了")
        texts = {d: (REPO_ROOT / d).read_text(encoding="utf-8") for d in _API_DOCS}
        undocumented = [
            f"{m:6} {p}   ({f}:{ln})"
            for m, p, f, ln in routes
            if not any(p in t for t in texts.values())
        ]
        self.assertEqual(
            [], undocumented,
            f"新端點沒進 API 表（{' 或 '.join(_API_DOCS)}）。路徑要**逐字**寫，"
            "含參數名與 `:path` 轉換器——`{id}` vs `{report_id}` 這種漂移，照抄"
            "就是 404：\n  " + "\n  ".join(undocumented),
        )

    def test_docs_reference_no_dead_endpoints(self):
        """反向：文件寫了、程式碼沒有的端點（刪掉端點後忘記改文件）。"""
        live = {p for _, p, _, _ in _declared_routes()}
        dead: list[str] = []
        for doc in _API_DOCS + ("CLAUDE.md",):
            text = (REPO_ROOT / doc).read_text(encoding="utf-8")
            for m in _BACKTICK.finditer(text):
                s = m.group(1).strip()
                if not s.startswith(("/api/", "/healthz")):
                    continue
                if " " in s or s.endswith("/") or any(c in s for c in "*?="):
                    continue            # 前綴寫法（`/api/*`）、帶查詢字串、散文
                if s not in live:
                    line = text.count("\n", 0, m.start()) + 1
                    dead.append(f"{doc}:{line}  `{s}`")
        self.assertEqual(
            [], dead,
            "文件寫的端點在程式碼裡不存在（路徑或參數名不符）：\n  " + "\n  ".join(dead),
        )


if __name__ == "__main__":
    unittest.main()
