# tests/test_env_loading.py
"""`web/env_loader.py` 的載入語意，以及 `web/server.py` 對它的接線。

════════════════════════════════════════════════════════════════════════
本檔絕對不能碰 repo root 的 .env
════════════════════════════════════════════════════════════════════════
這台機器上 repo root **就是部署目錄**：systemd 的 WorkingDirectory 指向它，
而 `web/server.py:23` 從**模組自身路徑**（不是 cwd）解析 `.env`——所以任何
「真的 import 一次 server」的端到端驗證，都只能靠覆寫真實 .env 才做得到。

舊版就是那樣寫的：`ENV_PATH.write_text(...)` 覆寫，再於 `finally` 還原。
2026-07-29 出事——還原沒有發生，生產的帳密與 `REPORT_MARK_SESSION_SECRET`
被換成本檔的測試值，重啟後生效。App 變成用一組**寫在 repo 裡**的密碼對外服務，
而簽章 secret 已知等於 session cookie 可被直接偽造、連登入都不必。
`finally` 擋得住例外，擋不住行程被殺（逾時、OOM、CI 取消）。

**任何「必須改動真實部署檔才驗得到」的測試，都是拿生產去換覆蓋率。**

改以三層互補，全部只碰暫存檔：

  1. 語意層：直接餵 `load_env_file()` 暫存檔，驗不做 shell 展開等契約
  2. 接線層：靜態驗 `server.py` 確實呼叫 loader、且路徑取自模組位置
  3. 順序層：靜態驗載入發生在 `web.deps` / `web.auth` 匯入之前

失去的只有「真的 import 一次」這個動作，而它換來的保證已由第 2、3 層以靜態
方式涵蓋。順帶解掉舊版 `timeout=5` 的假失敗（web.server 匯入在 WSL 要 4-8 秒）。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from web.env_loader import load_env_file  # noqa: E402

_SERVER_SRC = (REPO_ROOT / "web" / "server.py").read_text(encoding="utf-8")

# 測試用的鍵一律加前綴，避免與真實設定同名而互相污染。
_K = "TF_ENVTEST_"


class _EnvSandbox(unittest.TestCase):
    """每個測試前後只清自己碰過的鍵，不動其他任何環境變數。"""

    def setUp(self):
        self._touched: set[str] = set()

    def tearDown(self):
        for k in self._touched:
            os.environ.pop(k, None)

    def _load(self, content: str, *, override: bool = False) -> None:
        for line in content.splitlines():
            s = line.strip().removeprefix("export ").strip()
            if s and not s.startswith("#") and "=" in s:
                self._touched.add(s.split("=", 1)[0].strip())
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text(content, encoding="utf-8")
            load_env_file(p, override=override)


class LoaderSemanticsTests(_EnvSandbox):
    def test_no_shell_expansion(self):
        """值裡的 `$HOME` 必須原樣保留。

        這是 env_loader 存在的主要理由：把 .env 當 shell script 來 source，
        等於把 secrets 交給 shell 求值。
        """
        self._load(f'{_K}PW="prefix-$HOME-suffix"\n')
        self.assertEqual(os.environ[f"{_K}PW"], "prefix-$HOME-suffix")

    def test_no_command_substitution(self):
        for raw in ("$(id -u)", "`id -u`", "${HOME}"):
            with self.subTest(raw=raw):
                self._load(f'{_K}X="{raw}"\n')
                self.assertEqual(os.environ[f"{_K}X"], raw)
                os.environ.pop(f"{_K}X", None)

    def test_strips_only_outermost_matching_quotes(self):
        cases = [
            ('"abc"', "abc"),
            ("'abc'", "abc"),
            ('"a\'b"', "a'b"),      # 內層的不同引號原樣保留
            ('abc', "abc"),
            ('"abc', '"abc'),       # 不成對就不去
        ]
        for raw, want in cases:
            with self.subTest(raw=raw):
                self._load(f"{_K}Q={raw}\n")
                self.assertEqual(os.environ[f"{_K}Q"], want)
                os.environ.pop(f"{_K}Q", None)

    def test_comments_blanks_and_export_prefix(self):
        self._load(f"# 註解\n\n  \nexport {_K}A=1\n{_K}B=2\n#{_K}C=3\n")
        self.assertEqual(os.environ[f"{_K}A"], "1")
        self.assertEqual(os.environ[f"{_K}B"], "2")
        self.assertNotIn(f"{_K}C", os.environ)  # 被註解掉的不得載入

    def test_value_may_contain_equals(self):
        """base64 之類的值常含/以 `=` 結尾，只能切第一個 `=`。"""
        self._load(f"{_K}TOKEN=a=b=c==\n")
        self.assertEqual(os.environ[f"{_K}TOKEN"], "a=b=c==")

    def test_does_not_override_existing_by_default(self):
        """已存在的環境變數優先——部署時靠這個讓 systemd 的設定壓過 .env。"""
        os.environ[f"{_K}KEEP"] = "from-environ"
        self._touched.add(f"{_K}KEEP")
        self._load(f"{_K}KEEP=from-file\n")
        self.assertEqual(os.environ[f"{_K}KEEP"], "from-environ")

    def test_override_true_replaces(self):
        os.environ[f"{_K}OV"] = "old"
        self._touched.add(f"{_K}OV")
        self._load(f"{_K}OV=new\n", override=True)
        self.assertEqual(os.environ[f"{_K}OV"], "new")

    def test_missing_file_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            load_env_file(Path(d) / "nope.env")  # 不得拋例外


class ServerWiringTests(unittest.TestCase):
    """靜態驗接線，取代舊版「import 一次 server」——後者唯一的實現方式是覆寫真實 .env。"""

    def test_server_calls_loader_with_repo_root_env(self):
        self.assertIn("from web.env_loader import load_env_file", _SERVER_SRC)
        self.assertIn(
            'load_env_file(Path(__file__).resolve().parents[1] / ".env")', _SERVER_SRC,
            "server.py 必須從模組自身位置解析 .env；用 cwd 會隨啟動目錄而變",
        )

    def test_loader_runs_before_auth_and_deps_import(self):
        """順序是硬需求：auth 在 import 時就 fail-closed 檢查帳密，
        載入晚一步會變成「設定明明在 .env 裡卻拒絕啟動」。"""
        i_load = _SERVER_SRC.index("load_env_file(Path(__file__)")
        for mod in ("from web import deps", "from web import auth"):
            if mod in _SERVER_SRC:
                self.assertLess(
                    i_load, _SERVER_SRC.index(mod), f"{mod} 早於 .env 載入"
                )


class NoRealEnvMutationTests(unittest.TestCase):
    """守門：不得再出現寫入真實 .env 的程式碼，且跑完本檔後真檔逐位元不變。"""

    # 破壞性方法：對「指向 repo 根 .env 的變數」呼叫這些就是違規。
    _DESTRUCTIVE = {"write_text", "write_bytes", "unlink", "rename", "replace", "touch"}

    @classmethod
    def _repo_env_writers(cls, src: str) -> list[str]:
        """走 AST 找出「被指派為 repo 根 .env」的變數，回報對它做破壞性呼叫的方法名。

        **不能用字串比對**：第一版守門要求 `.env"` 後面緊跟 `write_text`，而舊版那
        兩件事隔了三十幾行——實測對舊版內容回 False，等於守了個寂寞。路徑指派與
        寫入之間可以隔任意距離，唯一可靠的關聯是「同一個名字」。
        """
        import ast

        tree = ast.parse(src)
        env_names: set[str] = set()
        for node in ast.walk(tree):
            # 形如 X = <任何東西> / ".env"
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.BinOp):
                rhs = node.value.right
                if isinstance(rhs, ast.Constant) and rhs.value == ".env":
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            env_names.add(t.id)
        found = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in cls._DESTRUCTIVE
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in env_names
            ):
                found.append(f"{node.func.value.id}.{node.func.attr}")
        return found

    def test_guard_actually_catches_the_old_pattern(self):
        """先證明守門有效，再拿它去掃全部測試——否則只是又一個假的守門。"""
        old = (
            'ENV_PATH = REPO_ROOT / ".env"\n'
            "def t():\n"
            "    original = ENV_PATH.read_text()\n"
            "    try:\n"
            "        pass\n"
            "    finally:\n"
            "        ENV_PATH.write_text(original)\n"
        )
        self.assertEqual(self._repo_env_writers(old), ["ENV_PATH.write_text"])
        safe = 'p = Path(d) / ".env"\np.write_text("x")\n'  # 暫存目錄下的 .env 不算
        self.assertEqual(self._repo_env_writers(safe), ["p.write_text"])

    def test_no_test_writes_repo_root_env(self):
        for p in sorted((REPO_ROOT / "tests").glob("test_*.py")):
            src = p.read_text(encoding="utf-8")
            # 只在該變數確實由 REPO_ROOT / parents[...] 組出時才算「repo 根」
            if not any(k in src for k in ("REPO_ROOT", "parents[")):
                continue
            writers = [
                w for w in self._repo_env_writers(src)
                if w.split(".")[0] not in {"p", "tmp", "tmp_path"}
            ]
            self.assertEqual(
                writers, [],
                f"{p.name} 對 repo 根 .env 做了 {writers}；測試不得改動真實部署檔"
                f"（見本檔模組 docstring 的 2026-07-29 事故）",
            )

    def test_repo_env_untouched(self):
        """真實 .env 若存在，載入暫存檔前後必須逐位元相同。"""
        real = REPO_ROOT / ".env"
        if not real.is_file():
            self.skipTest("此 checkout 沒有 .env")
        before = real.read_bytes()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text(f"{_K}PROBE=1\n", encoding="utf-8")
            load_env_file(p)
        os.environ.pop(f"{_K}PROBE", None)
        self.assertEqual(real.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
