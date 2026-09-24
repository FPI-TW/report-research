"""scripts/_llm_env.py：LLM 專用環境檔的載入順序與金鑰預檢（DeepSeek 遷移 PR-10）。

全程只碰 `tempfile` 產生的檔案，不碰 `/etc/default/report-mark-llm`，也不碰 repo 根 `.env`；
環境變數的改動都包在 `mock.patch.dict(os.environ)` 裡。金鑰一律是 gitleaks allowlist 內的假值。
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import _llm_env as le  # noqa: E402

FAKE_KEY = "fixed-test-secret-deepseek0"
PROJECT_ROOTS = {"app", "web", "scripts", "eval"}

# 「會呼叫 LLM」的判準：import 了呼叫層（線上串流、HTTP 客戶端、批次 CLI 包裝、批次鎖、評測 judge）。
# generate_brief.py 自帶 call_cli、不 import run_claude，是靠 `_claude_lock` 被掃到的。
LLM_MODULES = {
    "app.services.llm", "app.services.llm_http", "scripts._claude_cli", "scripts._claude_lock", "eval.judge",
}
LLM_SUBMODULE_NAMES = {"llm", "llm_http", "_claude_cli", "_claude_lock", "judge"}
LLM_SYMBOLS = {"run_claude", "stream_completion"}
KNOWN_ENTRIES = {
    "scripts/sync_new_reports.py", "scripts/tag_all_cli.py", "scripts/generate_summaries.py",
    "scripts/generate_titles.py", "scripts/extract_takeaways.py", "scripts/extract_signals.py",
    "scripts/generate_brief.py", "eval/run_ragas.py",
}


def _has_main_guard(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            left = node.test.left
            if isinstance(left, ast.Name) and left.id == "__name__":
                return True
    return False


def _imports_llm(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name in LLM_MODULES for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = {a.name for a in node.names}
            if node.module in LLM_MODULES or names & LLM_SYMBOLS:
                return True
            if node.module in {"app.services", "scripts", "eval"} and names & LLM_SUBMODULE_NAMES:
                return True
    return False


def _entry_files() -> dict[str, ast.Module]:
    out = {}
    for path in sorted((REPO_ROOT / "scripts").glob("*.py")) + sorted((REPO_ROOT / "eval").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if _has_main_guard(tree) and _imports_llm(tree):
            out[str(path.relative_to(REPO_ROOT))] = tree
    return out


def _is_sys_path_insert(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Attribute) and stmt.value.func.attr == "insert"
        and ast.unparse(stmt.value.func.value) == "sys.path"
    )


def _project_import(stmt: ast.stmt) -> str | None:
    if isinstance(stmt, ast.ImportFrom) and stmt.module and stmt.level == 0:
        return stmt.module if stmt.module.split(".")[0] in PROJECT_ROOTS else None
    if isinstance(stmt, ast.Import):
        for a in stmt.names:
            if a.name.split(".")[0] in PROJECT_ROOTS:
                return a.name
    return None


def _load_order_problem(tree: ast.Module) -> str | None:
    """sys.path.insert 之後的第一個專案 import 必須是 scripts._llm_env，下一句必須是 load_llm_env()。"""
    body = tree.body
    inserts = [i for i, s in enumerate(body) if _is_sys_path_insert(s)]
    if not inserts:
        return "找不到模組層的 sys.path.insert"
    before = [m for s in body[: inserts[0]] if (m := _project_import(s))]
    if before:
        return f"sys.path.insert 之前有專案 import：{before}"
    rest = body[inserts[0] + 1:]
    idx = next((i for i, s in enumerate(rest) if _project_import(s)), None)
    if idx is None or _project_import(rest[idx]) != "scripts._llm_env":
        return f"第一個專案 import 不是 scripts._llm_env：{idx is not None and _project_import(rest[idx])}"
    nxt = rest[idx + 1] if idx + 1 < len(rest) else None
    if not (
        isinstance(nxt, ast.Expr) and isinstance(nxt.value, ast.Call) and ast.unparse(nxt.value.func) == "load_llm_env"
    ):
        return f"緊接的下一句必須是 load_llm_env()，實得：{nxt is not None and ast.unparse(nxt)}"
    return None


def _calls(node: ast.AST, name: str) -> list[int]:
    return [
        n.lineno for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
    ]


class LoadOrderAstTests(unittest.TestCase):
    """掃描所有入口檔，不寫死清單（審查 L6）：新入口只要 import 了 LLM 呼叫層就會被掃到。"""

    def test_scanner_finds_known_entries(self):
        """掃描器本身的健全性：找不到已知入口代表判準壞了，下面的斷言會空轉。"""
        self.assertLessEqual(KNOWN_ENTRIES, set(_entry_files()))

    def test_llm_env_is_first_project_import_and_loaded_immediately(self):
        for rel, tree in _entry_files().items():
            with self.subTest(entry=rel):
                self.assertIsNone(_load_order_problem(tree))

    def test_checker_rejects_wrong_orders(self):
        """檢查器本身要抓得到錯：專案 import 搶先、或 import 了卻沒緊接著呼叫。"""
        head = "import sys\nsys.path.insert(0, 'x')\n"
        good = head + "from scripts._llm_env import load_llm_env\nload_llm_env()\nfrom app.services import db\n"
        self.assertIsNone(_load_order_problem(ast.parse(good)))
        for bad in (
            head + "from app.services import db\nfrom scripts._llm_env import load_llm_env\nload_llm_env()\n",
            head + "from scripts._llm_env import load_llm_env\nfrom app.services import db\nload_llm_env()\n",
            "from app.services import db\n" + head,
            "import os\n",
        ):
            self.assertIsNotNone(_load_order_problem(ast.parse(bad)), bad)

    def test_require_llm_key_is_called_before_lock_in_same_scope(self):
        for rel, tree in _entry_files().items():
            with self.subTest(entry=rel):
                self.assertTrue(_calls(tree, "require_llm_key"), "入口必須呼叫 require_llm_key")
                scopes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.If))]
                for scope in scopes:
                    locks = [
                        n.lineno for stmt in scope.body for n in ast.walk(stmt)
                        if isinstance(n, ast.Call) and ast.unparse(n.func) == "claude_cli_lock_or_exit"
                    ]
                    requires = [ln for stmt in scope.body for ln in _calls(stmt, "require_llm_key")]
                    if locks and requires:
                        self.assertLess(min(requires), min(locks), "require_llm_key 要在取鎖之前")


class _EnvFileCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 檔名刻意不叫 `.env`：tests/test_env_loading.py 的 AST 守門依檔名判定寫入目標
        self.path = Path(self._tmp.name) / "llm.env"
        patcher = mock.patch.dict(os.environ, {"LLM_ENV_FILE": str(self.path)})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("DEEPSEEK_API_KEY", None)
        le._STATE.clear()
        self.addCleanup(le._STATE.clear)
        # 不讓 worktree 警告干擾輸出斷言（另有專門的測試）
        wt = mock.patch.object(le, "_warn_if_not_deploy_root")
        wt.start()
        self.addCleanup(wt.stop)

    def write(self, text: str) -> None:
        self.path.write_text(text, encoding="utf-8")

    def require(self, models) -> tuple[int | None, str]:
        err = io.StringIO()
        code = None
        with contextlib.redirect_stderr(err):
            try:
                le.require_llm_key(models)
            except SystemExit as exc:
                code = exc.code
        return code, err.getvalue()


class LoadTests(_EnvFileCase):
    def test_only_fills_missing_keys(self):
        self.write(f"DEEPSEEK_API_KEY={FAKE_KEY}\nSUMMARY_MODEL=deepseek-flash\nTITLE_MODEL=deepseek-flash\n")
        os.environ["TITLE_MODEL"] = "claude-sonnet-5"
        os.environ.pop("SUMMARY_MODEL", None)
        le.load_llm_env()
        self.assertEqual(os.environ["DEEPSEEK_API_KEY"], FAKE_KEY)
        self.assertEqual(os.environ["SUMMARY_MODEL"], "deepseek-flash")
        self.assertEqual(os.environ["TITLE_MODEL"], "claude-sonnet-5", "shell 顯式設的值優先")

    def test_missing_file_is_not_an_error_at_load(self):
        le.load_llm_env()
        self.assertEqual(le._STATE["error"], "missing")

    def test_permission_error_is_recorded_not_raised(self):
        self.write(f"DEEPSEEK_API_KEY={FAKE_KEY}\n")
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            le.load_llm_env()
        self.assertEqual(le._STATE["error"], "permission")
        self.assertNotIn("DEEPSEEK_API_KEY", os.environ)

    def test_duplicate_keys_are_not_loaded(self):
        self.write(f"DEEPSEEK_API_KEY={FAKE_KEY}\nDEEPSEEK_API_KEY=fixed-test-secret-deepseek1\n")
        le.load_llm_env()
        self.assertEqual(le._STATE["duplicates"], ["DEEPSEEK_API_KEY"])
        self.assertNotIn("DEEPSEEK_API_KEY", os.environ)


class RequireTests(_EnvFileCase):
    def test_all_claude_needs_no_key(self):
        le.load_llm_env()  # 檔案不存在
        code, out = self.require(["claude-sonnet-5", "claude-haiku-4-5"])
        self.assertIsNone(code)
        self.assertNotIn("fp=", out)

    def test_key_present_prints_fingerprint_never_the_key(self):
        self.write(f"DEEPSEEK_API_KEY={FAKE_KEY}\n")
        le.load_llm_env()
        code, out = self.require(["deepseek-flash", "claude-sonnet-5"])
        self.assertIsNone(code)
        self.assertIn(f"fp={hashlib.sha256(FAKE_KEY.encode()).hexdigest()[:8]}", out)
        self.assertNotIn(FAKE_KEY, out)

    def test_duplicate_keys_rc2_even_when_all_claude(self):
        """重複鍵可能是模型旋鈕本身：哪一行生效取決於讀的人，任何情況都拒跑。"""
        self.write("SUMMARY_MODEL=claude-sonnet-5\nSUMMARY_MODEL=deepseek-flash\n")
        le.load_llm_env()
        code, out = self.require(["claude-sonnet-5"])
        self.assertEqual(code, 2)
        self.assertIn("SUMMARY_MODEL", out)

    def test_unknown_model_rc2(self):
        le.load_llm_env()
        for name in ("sonnet", "deepseek-flsh"):
            code, out = self.require([name])
            self.assertEqual(code, 2, name)
            self.assertIn("未知模型名", out)

    def test_permission_error_rc2_hints_kashionz_without_leaking_key(self):
        self.write(f"DEEPSEEK_API_KEY={FAKE_KEY}\n")
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            le.load_llm_env()
        code, out = self.require(["deepseek-flash"])
        self.assertEqual(code, 2)
        self.assertIn("kashionz", out)
        self.assertNotIn(FAKE_KEY, out)

    def test_empty_env_value_blocks_file_and_hints_unset(self):
        """shell 裡 export 了空值：只補不存在的鍵，所以檔裡的金鑰進不來。"""
        self.write(f"DEEPSEEK_API_KEY={FAKE_KEY}\n")
        os.environ["DEEPSEEK_API_KEY"] = ""
        le.load_llm_env()
        code, out = self.require(["deepseek-flash"])
        self.assertEqual(code, 2)
        self.assertIn("unset DEEPSEEK_API_KEY", out)
        self.assertNotIn(FAKE_KEY, out)

    def test_missing_file_hints_install(self):
        le.load_llm_env()
        code, out = self.require(["deepseek-flash"])
        self.assertEqual(code, 2)
        self.assertIn("report-mark-llm.env.example", out)

    def test_file_without_key_hints_sudoedit(self):
        self.write("DEEPSEEK_API_KEY=\n")
        le.load_llm_env()
        code, out = self.require(["deepseek-flash"])
        self.assertEqual(code, 2)
        self.assertIn("sudoedit", out)


class WorktreeWarningTests(unittest.TestCase):
    def _warn(self, env: dict, worktree: bool) -> str:
        err = io.StringIO()
        with mock.patch.dict(os.environ, env), mock.patch.object(le, "_is_worktree", return_value=worktree):
            with contextlib.redirect_stderr(err):
                le._warn_if_not_deploy_root()
        return err.getvalue()

    def test_warns_when_root_differs_from_deploy_root(self):
        out = self._warn({"REPORT_MARK_ROOT": "/somewhere/else"}, worktree=False)
        self.assertIn("不與主 checkout 互斥", out)

    def test_warns_in_linked_worktree(self):
        self.assertIn("不是部署目錄", self._warn({"REPORT_MARK_ROOT": ""}, worktree=True))

    def test_quiet_in_deploy_checkout(self):
        self.assertEqual(self._warn({"REPORT_MARK_ROOT": str(le.ROOT)}, worktree=False), "")


class ConftestTests(unittest.TestCase):
    def test_tests_never_read_the_real_llm_env_file(self):
        self.assertNotEqual(le.env_file_path(), Path(le.DEFAULT_LLM_ENV_FILE))
        self.assertFalse(le.env_file_path().exists())


if __name__ == "__main__":
    unittest.main()
