# tests/test_framework_branding.py
"""框架擁有的檔案不得出現「廷豐」／tingfong：機構專屬的東西放 `profiles/tingfong/` 或廷豐主機層。

框架擁有的範圍＝`app/`、`web/`、`frontend/src/`、`frontend/index.html`、`eval/` 的程式，加上
`deploy/OWNERSHIP.toml` 歸為 framework 的檔案（`scripts/`、`deploy/container/`、`deploy/compose/`、`deploy/demo/`…）。
歸為 tingfong_host 的檔案（systemd unit、維運代理、探針、sync 殼…）、`profiles/tingfong/`、測試與 fixture 不在範圍內。

程式行為裡的機構值（網址、名稱、色票）一律由 profile 提供；docstring／註解指稱「預設 profile」而不是機構名。
真的非寫不可的（預設 profile 的目錄名本身）列在 `ALLOWED`：每個檔案釘住**行數**與理由，同一個檔案多一行也會紅。
紅了是改程式或註解，不是放寬白名單。
"""
from __future__ import annotations

import re
import subprocess
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(r"廷豐|tingfong", re.IGNORECASE)
FRAMEWORK_DIRS = ("app", "web", "frontend/src", "frontend/index.html", "eval")
# 不是程式、而是預設 profile 的資料（凍結題集與其基準線），檔名或內容本來就屬於那個 profile。
DATA_SUFFIXES = (".json", ".jsonl")

# 檔案 → (允許的行數, 理由)
ALLOWED: dict[str, tuple[int, str]] = {
    "app/profile.py": (2, "預設 profile 的目錄 `profiles/tingfong`（`DEFAULT_PROFILE_DIR` 與說明它的 docstring）"
                          "是已定案的共識"),
    "eval/profile_eval.py": (8, "預設 profile 的題集沿用 repo 內 `eval/ragas_questions.json` 的相容路徑："
                                "同名常數與逐條說明（`profiles/tingfong/eval/eval.toml` 的 `legacy_repo_path`"
                                "指回它）"),
    "deploy/compose/.env.example": (1, "`PROFILE_HOST_DIR` 的範例值與程式的預設 profile 一致"
                                       "（標【必填】，部署方換成自己的）"),
}


def _tracked(*paths: str) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", "--", *paths], cwd=REPO_ROOT, check=True,
                         capture_output=True).stdout.decode("utf-8")
    return [p for p in out.split("\0") if p]


def framework_files() -> list[str]:
    manifest = tomllib.loads((REPO_ROOT / "deploy" / "OWNERSHIP.toml").read_text(encoding="utf-8"))
    files = set(manifest["framework"]["files"])
    files.update(f for f in _tracked(*FRAMEWORK_DIRS) if not f.endswith(DATA_SUFFIXES))
    files.discard("deploy/OWNERSHIP.toml")  # 清單本身要寫出主機層的類別名
    return sorted(files)


def mentions(path: str) -> list[str]:
    data = (REPO_ROOT / path).read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:  # 圖檔、字型等二進位檔
        return []
    return [f"{path}:{i}: {line.strip()[:120]}" for i, line in enumerate(text.splitlines(), 1) if PATTERN.search(line)]


class FrameworkBrandingTests(unittest.TestCase):
    def test_framework_files_do_not_name_the_institution(self):
        unexpected: list[str] = []
        for path in framework_files():
            found = mentions(path)
            allowed = ALLOWED.get(path, (0, ""))[0]
            if len(found) != allowed:
                unexpected.append(f"{path}（允許 {allowed} 行、實際 {len(found)} 行）\n    " + "\n    ".join(found))
        self.assertFalse(unexpected,
                         "框架擁有的檔案出現機構名：改由 profile 提供或改寫成框架用語\n" + "\n".join(unexpected))

    def test_allowlist_entries_are_in_scope_and_explained(self):
        scope = set(framework_files())
        for path, (count, reason) in ALLOWED.items():
            with self.subTest(path=path):
                self.assertIn(path, scope, "白名單的檔案不在框架範圍內（搬走或改歸屬了就刪掉這條）")
                self.assertGreater(count, 0)
                self.assertTrue(reason.strip())

    def test_scope_covers_the_named_directories(self):
        """範圍真的涵蓋規格點名的目錄（防止 OWNERSHIP 改壞後掃描範圍靜默變小）。"""
        scope = framework_files()
        for prefix in ("app/", "web/", "frontend/src/", "scripts/", "deploy/container/", "deploy/compose/"):
            with self.subTest(prefix=prefix):
                self.assertTrue(any(p.startswith(prefix) for p in scope), prefix)
        self.assertIn("web/static/login.html", scope)
        self.assertNotIn("scripts/incident_handler.sh", scope)  # 主機層不在範圍內


if __name__ == "__main__":
    unittest.main()
