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
DIRECT_LLM_MODULES = {
    "app.services.llm", "app.services.llm_http", "scripts._claude_cli", "scripts._claude_lock", "eval.judge",
}
# 間接呼叫 LLM 的服務層（審查低 4）：問答受控重播這類入口不直接 import 呼叫層，只 import
# answer／retrieval_pipeline 等，模型常數卻一樣在 import 期解析——漏掃就會用「沒讀到 llm 檔」的設定。
INDIRECT_SUBMODULES = {
    "answer", "retrieval_pipeline", "faithfulness", "scope_router", "query_planner", "agentic_qa", "followups",
}
INDIRECT_LLM_MODULES = {f"app.services.{m}" for m in INDIRECT_SUBMODULES}
LLM_MODULES = DIRECT_LLM_MODULES | INDIRECT_LLM_MODULES
LLM_SUBMODULE_NAMES = {"llm", "llm_http", "_claude_cli", "_claude_lock", "judge"} | INDIRECT_SUBMODULES
LLM_SYMBOLS = {"run_claude", "stream_completion"}
KNOWN_ENTRIES = {
    "scripts/sync_new_reports.py", "scripts/tag_all_cli.py", "scripts/generate_summaries.py",
    "scripts/generate_titles.py", "scripts/extract_takeaways.py", "scripts/extract_signals.py",
    "scripts/generate_brief.py", "eval/run_ragas.py",
}
# 被間接層判準掃到、但**不呼叫 LLM** 的入口：只從間接層取常數或純函式。逐檔列出允許取用的名稱
# （含經模組別名取用的屬性）；一旦取用了其他名稱（例如 answer_question），豁免失效、測試紅，
# 那時要照規矩載入 llm 檔並預檢，而不是擴充這份清單了事。
NON_LLM_ENTRIES: dict[str, set[str]] = {
    "scripts/analyze_qa_log.py": {"NO_CONTEXT_MESSAGE", "OFF_TOPIC_MESSAGES"},
    "scripts/eval_retrieval.py": {
        "_as_date", "build_context", "RECENCY_HALF_LIFE_DAYS", "RELEVANCE_BAND", "BAND_EPS", "ASK_DENSE_SCAN",
        "RETRIEVAL_K",
    },
    "eval/dataset.py": {"NO_CONTEXT_MESSAGE", "OFF_TOPIC_MESSAGES", "TIME_SENSITIVE_UNAVAILABLE_MESSAGE"},
}


def _has_main_guard(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            left = node.test.left
            if isinstance(left, ast.Name) and left.id == "__name__":
                return True
    return False


def _imports_llm(tree: ast.Module, modules=None, submodules=None) -> bool:
    modules = LLM_MODULES if modules is None else modules
    submodules = LLM_SUBMODULE_NAMES if submodules is None else submodules
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name in modules for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = {a.name for a in node.names}
            if node.module in modules or names & LLM_SYMBOLS:
                return True
            if node.module in {"app.services", "scripts", "eval"} and names & submodules:
                return True
    return False


def _indirect_names(tree: ast.Module) -> set[str]:
    """從間接層取用的名稱：`from app.services.answer import X` 的 X，與模組別名上的屬性。"""
    names: set[str] = set()
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in INDIRECT_LLM_MODULES:
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module == "app.services":
            aliases |= {a.asname or a.name for a in node.names if a.name in INDIRECT_SUBMODULES}
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name in INDIRECT_LLM_MODULES:
                    names.add(f"<import {a.name}>")  # 整條路徑 import：無從逐一核對，一律不豁免
    accounted: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases:
            names.add(node.attr)
            accounted.add(id(node.value))
        elif (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"
            and len(node.args) >= 2 and isinstance(node.args[0], ast.Name) and node.args[0].id in aliases
            and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)
        ):
            names.add(node.args[1].value)
            accounted.add(id(node.args[0]))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in aliases and id(node) not in accounted:
            names.add(f"<bare {node.id}>")  # 別名整個被傳出去：無從逐一核對
    return names


def _scanned_files() -> dict[str, ast.Module]:
    out = {}
    for path in sorted((REPO_ROOT / "scripts").glob("*.py")) + sorted((REPO_ROOT / "eval").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if _has_main_guard(tree) and _imports_llm(tree):
            out[str(path.relative_to(REPO_ROOT))] = tree
    return out


def _entry_files() -> dict[str, ast.Module]:
    return {rel: tree for rel, tree in _scanned_files().items() if rel not in NON_LLM_ENTRIES}


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

    def test_scanner_catches_indirect_entries(self):
        """只 import 間接層（例如之後的問答受控重播腳本）也算入口。"""
        main = "\nif __name__ == '__main__':\n    pass\n"
        for src in (
            "from app.services.retrieval_pipeline import retrieve_context",
            "from app.services import answer",
            "from app.services.answer import answer_question",
            "from app.services.faithfulness import check_answer",
            "import app.services.scope_router",
        ):
            with self.subTest(src=src):
                self.assertTrue(_imports_llm(ast.parse(src + main)))
        self.assertFalse(_imports_llm(ast.parse("from app.services.db import SessionFactory" + main)))

    def test_non_llm_exemptions_are_exact_and_still_non_llm(self):
        """豁免的入口：必須仍被掃到（否則清單過期）、不 import 直接呼叫層、只取用列出的名稱。"""
        scanned = _scanned_files()
        for rel, allowed in NON_LLM_ENTRIES.items():
            with self.subTest(entry=rel):
                self.assertIn(rel, scanned, "不再被掃到就從 NON_LLM_ENTRIES 移除")
                tree = scanned[rel]
                self.assertFalse(
                    _imports_llm(tree, DIRECT_LLM_MODULES, {"llm", "llm_http", "_claude_cli", "_claude_lock", "judge"}),
                    "import 了直接呼叫層就不是「不呼叫 LLM」",
                )
                self.assertLessEqual(_indirect_names(tree), allowed, "取用了清單外的名稱：改為照規矩載入與預檢")

    def test_indirect_name_collector(self):
        tree = ast.parse(
            "from app.services import answer as _a\nfrom app.services.answer import X\n"
            "print(_a.Y, getattr(_a, 'Z', 1))\nf(_a)\n"
        )
        self.assertEqual(_indirect_names(tree), {"X", "Y", "Z", "<bare _a>"})

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

    def require(self, models, *, http_dispatch: bool = True) -> tuple[int | None, str]:
        """預設 `http_dispatch=True`（評測入口的語意）：金鑰那幾條與批次拒收是兩件事，分開測。"""
        err = io.StringIO()
        code = None
        with contextlib.redirect_stderr(err):
            try:
                le.require_llm_key(models, http_dispatch=http_dispatch)
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


class BatchRejectsHttpModelTests(_EnvFileCase):
    """PR-12 之前批次沒有 HTTP 分派：解析到 DeepSeek 名稱一律 rc=2，並說出是哪個旋鈕解析出來的。

    不擋的話，`claude --model deepseek-flash` 每一篇都失敗：行內標註全數 skip_untagged＝新研報
    停止入庫（見 scripts/_claude_cli.py 的 HttpModelUnsupportedError）。
    """

    def setUp(self):
        super().setUp()
        for k in ("LLM_PROVIDER", "TITLE_MODEL", "TAG_MODEL", "SIGNAL_MODEL"):
            os.environ[k] = ""

    def test_knob_is_named(self):
        os.environ["TITLE_MODEL"] = "deepseek-flash"
        le.load_llm_env()
        code, out = self.require({"title": "deepseek-flash"}, http_dispatch=False)
        self.assertEqual(code, 2)
        self.assertIn("PR-12", out)
        self.assertIn("TITLE_MODEL=deepseek-flash", out)

    def test_provider_default_is_named(self):
        os.environ["LLM_PROVIDER"] = "deepseek"
        le.load_llm_env()
        code, out = self.require({"tag": "deepseek-flash"}, http_dispatch=False)
        self.assertEqual(code, 2)
        self.assertIn("LLM_PROVIDER=deepseek", out)
        self.assertIn("tag", out)

    def test_cli_flag_is_named(self):
        le.load_llm_env()
        code, out = self.require({"signal": "deepseek-v4-pro"}, http_dispatch=False)
        self.assertEqual(code, 2)
        self.assertIn("--model deepseek-v4-pro", out)

    def test_rejected_even_with_key(self):
        """有金鑰也擋：擋的理由是批次不會分派，不是缺金鑰。"""
        self.write(f"DEEPSEEK_API_KEY={FAKE_KEY}\n")
        le.load_llm_env()
        code, out = self.require({"title": "deepseek-flash"}, http_dispatch=False)
        self.assertEqual(code, 2)
        self.assertIn("PR-12", out)
        self.assertNotIn("fp=", out)
        self.assertNotIn(FAKE_KEY, out)

    def test_plain_list_defaults_to_batch(self):
        """預設是批次語意：忘了傳 http_dispatch 的新入口要被擋，而不是放行。"""
        le.load_llm_env()
        code, out = self.require(["deepseek-flash"], http_dispatch=False)
        self.assertEqual(code, 2)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            le.require_llm_key(["deepseek-flash"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("PR-12", err.getvalue())

    def test_claude_models_pass(self):
        le.load_llm_env()
        code, _ = self.require({"title": "claude-sonnet-5", "tag": "claude-haiku-4-5"}, http_dispatch=False)
        self.assertIsNone(code)

    def test_every_batch_entry_uses_batch_semantics(self):
        """批次入口不得傳 http_dispatch=True（PR-12 才放開）；評測入口才可以。"""
        allowed = {"eval/run_ragas.py"}
        for rel, tree in _entry_files().items():
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "require_llm_key":
                    kws = {k.arg: ast.unparse(k.value) for k in node.keywords}
                    with self.subTest(entry=rel):
                        if rel in allowed:
                            self.assertEqual(kws.get("http_dispatch"), "True")
                        else:
                            self.assertNotIn("http_dispatch", kws)


class FileKeyFingerprintTests(unittest.TestCase):
    """金鑰指紋的比對指令（docs/production_resilience.md）與預檢、web 同一套解析。"""

    def _fp(self, text: str) -> str | None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "llm.env"
            path.write_bytes(text.encode("utf-8"))
            return le.file_key_fingerprint(path)

    def test_quotes_whitespace_and_crlf_do_not_change_fingerprint(self):
        want = le.fingerprint(FAKE_KEY)
        for text in (
            f"DEEPSEEK_API_KEY={FAKE_KEY}\n",
            f'DEEPSEEK_API_KEY="{FAKE_KEY}"\n',
            f"DEEPSEEK_API_KEY='{FAKE_KEY}'\n",
            f"DEEPSEEK_API_KEY={FAKE_KEY}   \n",
            f"DEEPSEEK_API_KEY={FAKE_KEY}\r\n",
            f"export DEEPSEEK_API_KEY={FAKE_KEY}\n",
            f"# 註解\nOTHER=1\nDEEPSEEK_API_KEY={FAKE_KEY}",
        ):
            with self.subTest(text=text):
                self.assertEqual(self._fp(text), want)

    def test_missing_or_empty_key_is_none(self):
        self.assertIsNone(self._fp("OTHER=1\n"))
        self.assertIsNone(self._fp("DEEPSEEK_API_KEY=\n"))
        self.assertIsNone(self._fp('DEEPSEEK_API_KEY=""\n'))

    def test_cli_prints_only_prefix_and_rc_reflects_match(self):
        with tempfile.TemporaryDirectory() as d:
            a, b, c = Path(d) / "web-copy", Path(d) / "llm-copy", Path(d) / "other-copy"
            a.write_text(f"DEEPSEEK_API_KEY={FAKE_KEY}\n", encoding="utf-8")
            b.write_text(f'DEEPSEEK_API_KEY="{FAKE_KEY}"\r\n', encoding="utf-8")
            c.write_text("DEEPSEEK_API_KEY=fixed-test-secret-deepseek1\n", encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(le.main([str(a), str(b)]), 0)
            self.assertIn(le.fingerprint(FAKE_KEY), out.getvalue())
            self.assertNotIn(FAKE_KEY, out.getvalue())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(le.main([str(a), str(c)]), 1)
                self.assertEqual(le.main([str(a), str(Path(d) / "missing")]), 1)


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
