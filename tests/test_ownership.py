# tests/test_ownership.py
"""`deploy/OWNERSHIP.toml`：deploy/、scripts/、ops_agent/ 每個受版控檔案都歸到「框架」或「廷豐主機層」其中一類。

守的是：
- 新檔案沒有歸類就紅（逐檔列出、不用萬用字元，歸類是一個要寫下來的決定）；列了但不存在的也紅。
- 一個檔案只能有一個歸屬。
- 框架不依賴主機層：容器排程（`deploy/container/scheduler.py` 的 `JOBS`）與 entrypoint 只呼叫框架的腳本，
  框架的 Python（`app/`、`web/`、`deploy/container/`、框架的 `scripts/`）不 import 主機層的腳本。

掃的是 git 索引（`git ls-files`）：還沒 `git add` 的新檔不在範圍內，CI 上照樣會被抓到。
"""
from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from deploy.container import scheduler as sch  # noqa: E402

MANIFEST = REPO_ROOT / "deploy" / "OWNERSHIP.toml"
SCOPES = ("deploy", "scripts", "ops_agent")
CATEGORIES = ("framework", "tingfong_host")


def tracked(*paths: str) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", "--", *paths], cwd=REPO_ROOT, check=True,
                         capture_output=True).stdout.decode("utf-8")
    return sorted(p for p in out.split("\0") if p)


def load_manifest() -> dict[str, list[str]]:
    data = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    return {cat: list(data[cat]["files"]) for cat in CATEGORIES}


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest()

    def test_only_known_categories(self):
        data = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(sorted(data), sorted(CATEGORIES))
        for cat in CATEGORIES:
            self.assertEqual(sorted(data[cat]), ["files"], cat)

    def test_no_wildcards_and_no_duplicates(self):
        for cat, files in self.manifest.items():
            with self.subTest(category=cat):
                self.assertEqual(len(files), len(set(files)), "同一類裡有重複的檔名")
                for f in files:
                    self.assertFalse(any(ch in f for ch in "*?[]"), f"不用萬用字元：{f}")
        both = set(self.manifest["framework"]) & set(self.manifest["tingfong_host"])
        self.assertFalse(both, f"同一個檔案歸到兩類：{sorted(both)}")

    def test_every_tracked_file_is_classified(self):
        listed = set(self.manifest["framework"]) | set(self.manifest["tingfong_host"])
        missing = [f for f in tracked(*SCOPES) if f not in listed]
        self.assertFalse(missing, "這些檔案沒有歸類，在 deploy/OWNERSHIP.toml 加進 framework 或 tingfong_host："
                         + "、".join(missing))

    def test_no_stale_entries(self):
        present = set(tracked(*SCOPES))
        for cat, files in self.manifest.items():
            stale = [f for f in files if f not in present]
            self.assertFalse(stale, f"{cat} 列了不存在（或不在 {SCOPES} 底下）的檔案：{stale}")


def _framework_python_files(manifest: dict[str, list[str]]) -> list[str]:
    own = [f for f in manifest["framework"] if f.endswith(".py")]
    return own + [f for f in tracked("app", "web") if f.endswith(".py")]


def _imported_scripts(source: str) -> set[str]:
    """`import scripts.x`、`from scripts import x`、`from scripts.x import …` 引用到的 scripts 模組名。"""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[1] for a in node.names if a.name.startswith("scripts."))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module == "scripts":
                names.update(a.name for a in node.names)
            elif node.module.startswith("scripts."):
                names.add(node.module.split(".")[1])
    return names


class FrameworkIndependenceTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest()
        self.host = set(self.manifest["tingfong_host"])

    def test_container_jobs_only_call_framework_scripts(self):
        for job in sch.JOBS:
            for arg in job.command:
                if arg.startswith("scripts/"):
                    with self.subTest(job=job.name):
                        self.assertIn(arg, self.manifest["framework"], f"{job.name} 呼叫的 {arg} 不是框架的檔案")
        entry = (REPO_ROOT / "deploy" / "container" / "entrypoint.py").read_text(encoding="utf-8")
        self.assertIn('"process_uploads.sh"', entry)
        self.assertIn("scripts/process_uploads.sh", self.manifest["framework"])

    def test_framework_python_does_not_import_host_scripts(self):
        host_modules = {Path(f).stem for f in self.host if f.startswith("scripts/") and f.endswith(".py")}
        offenders = []
        for f in _framework_python_files(self.manifest):
            used = _imported_scripts((REPO_ROOT / f).read_text(encoding="utf-8")) & host_modules
            offenders += [f"{f} → scripts/{m}.py" for m in sorted(used)]
        self.assertFalse(offenders, "框架的程式 import 了廷豐主機層的腳本：" + "、".join(offenders))


if __name__ == "__main__":
    unittest.main()
