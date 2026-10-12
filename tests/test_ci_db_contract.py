"""DB 契約測試的清單與 CI schema job 的步驟逐支對帳（不連 DB、不跑 CI）。

DB 契約測試在後端 job（沒有 DB）只會 skip；真正跑到它們的只有 `.github/workflows/ci.yml`
的 schema job，而那個 job 是逐支列步驟（`uv run pytest -q tests/...`）。新增一支卻忘了加步驟，
它在 CI 上就永遠是 skipped、全綠而零覆蓋，沒有任何訊號。

集合 A：`tests/test_*.py` 裡以字串常數 `REPORT_MARK_REQUIRE_DB` 參照該旗標的檔案（AST 判定，
docstring 裡順帶提到不算）；另外所有 `tests/test_*_db.py` 都必須在 A 裡——命名成 DB 契約測試
卻不認這個旗標，CI 上連不上 DB 也會靜默 skip。
集合 B：schema job 裡以 `uv run pytest` 執行的 `tests/*.py`。A 必須等於 B。

ci.yml 刻意用純文字解析：pyyaml 只是遞移相依，不該為了一支守門測試變成直接相依。
"""

from __future__ import annotations

import ast
import re
import unittest
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
FLAG = "REPORT_MARK_REQUIRE_DB"

# 反向守門：A 的收集方式壞掉（旗標改名、AST 判定失效）時 A 與 B 可能雙雙變空、照樣相等。
# 寫這支測試時是 23 支；下限留一點餘裕，刪掉一兩支不必改這裡，掉到這以下就是收集方式出了事。
MIN_DB_CONTRACT_TESTS = 20

# 不是 `*_db.py` 命名的已知成員（AGENTS.md「測試與 CI」點名的兩支）：收集必須看得到它們。
KNOWN_NON_DB_SUFFIX_MEMBERS = ("tests/test_schema_constraints.py", "tests/test_content_norm_equivalence.py")


def _references_flag(path: Path) -> bool:
    with warnings.catch_warnings():  # 別支測試 docstring 裡的跳脫字元警告與本測試無關
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(isinstance(node, ast.Constant) and node.value == FLAG for node in ast.walk(tree))


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _db_contract_tests() -> set[str]:
    """集合 A。本檔自己也寫著旗標名稱，排除。"""
    me = Path(__file__).resolve()
    return {
        _rel(p) for p in sorted(TESTS_DIR.glob("test_*.py")) if p.resolve() != me and _references_flag(p)
    }


def _schema_job_lines(text: str) -> list[str]:
    """`jobs:` 底下 `schema:` 這個 job 的原始行（到下一個同層 job 或檔尾為止）。"""
    lines = text.splitlines()
    try:
        jobs_at = next(i for i, ln in enumerate(lines) if re.fullmatch(r"jobs:\s*(#.*)?", ln))
    except StopIteration:
        raise AssertionError(f"{_rel(CI_YML)} 找不到頂層 jobs:") from None
    job_indent = None
    start = None
    for i in range(jobs_at + 1, len(lines)):
        ln = lines[i]
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        indent = len(ln) - len(ln.lstrip(" "))
        if indent == 0:  # 離開 jobs:
            break
        if job_indent is None:
            job_indent = indent
        if indent == job_indent and start is not None:
            return lines[start:i]
        if indent == job_indent and re.fullmatch(r"schema:\s*(#.*)?", ln.strip()):
            start = i + 1
    if start is None:
        raise AssertionError(f"{_rel(CI_YML)} 的 jobs: 底下找不到 schema job（改名了就同步本測試）")
    end = next((i for i in range(start, len(lines)) if lines[i] and not lines[i].startswith(" ")), len(lines))
    return lines[start:end]


def _ci_schema_pytest_files(text: str | None = None) -> set[str]:
    """集合 B：schema job 裡、非註解行上 `uv run pytest` 指令帶到的每一個 tests/*.py。"""
    if text is None:
        text = CI_YML.read_text(encoding="utf-8")
    found: set[str] = set()
    for ln in _schema_job_lines(text):
        if ln.lstrip().startswith("#") or "uv run pytest" not in ln:
            continue
        cmd = ln.split("uv run pytest", 1)[1]
        found.update(re.findall(r"(?<![\w/.-])(tests/[\w/.-]+\.py)\b", cmd))
    return found


class CiDbContractTests(unittest.TestCase):
    def test_every_db_contract_test_runs_in_schema_job(self):
        a, b = _db_contract_tests(), _ci_schema_pytest_files()
        missing = sorted(a - b)
        extra = sorted(b - a)
        self.assertEqual(
            [], missing,
            f"這些 DB 契約測試沒有在 {_rel(CI_YML)} 的 schema job 執行（CI 上只會靜默 skip），"
            f"請各加一個 `uv run pytest -q <檔案>` 步驟：{missing}",
        )
        self.assertEqual(
            [], extra,
            f"schema job 執行的這些檔案不參照 {FLAG}（DB 不在時不會失敗，或檔案已不存在）：{extra}",
        )

    def test_db_suffix_tests_honour_require_flag(self):
        a = _db_contract_tests()
        offenders = sorted(_rel(p) for p in TESTS_DIR.glob("test_*_db.py") if _rel(p) not in a)
        self.assertEqual(
            [], offenders,
            f"命名為 DB 契約測試卻不認 {FLAG}（連不上 DB 時在 CI 也只會 skip）：{offenders}",
        )

    def test_collection_is_not_vacuous(self):
        a = _db_contract_tests()
        self.assertGreater(
            len(a), MIN_DB_CONTRACT_TESTS,
            f"只收集到 {len(a)} 支 DB 契約測試，低於下限 {MIN_DB_CONTRACT_TESTS}：收集方式可能失效",
        )
        for known in KNOWN_NON_DB_SUFFIX_MEMBERS:
            with self.subTest(known=known):
                self.assertIn(known, a)
        self.assertGreater(len(_ci_schema_pytest_files()), MIN_DB_CONTRACT_TESTS)

    def test_parser_reads_schema_job_only(self):
        """解析器的對照組：註解行、別的 job 的 pytest 都不算進 B。"""
        text = (
            "on: push\n"
            "jobs:\n"
            "  backend:\n"
            "    steps:\n"
            "      - run: uv run pytest -q tests/test_backend_only.py\n"
            "  schema:\n"
            "    name: schema\n"
            "    steps:\n"
            "      # - run: uv run pytest -q tests/test_commented_out.py\n"
            "      - name: a\n"
            "        run: uv run pytest -q tests/test_a_db.py\n"
            "      - name: b\n"
            "        run: |\n"
            "          uv run pytest -q tests/test_b_db.py tests/test_c.py\n"
            "  secrets:\n"
            "    steps:\n"
            "      - run: uv run pytest -q tests/test_after.py\n"
        )
        self.assertEqual(
            {"tests/test_a_db.py", "tests/test_b_db.py", "tests/test_c.py"}, _ci_schema_pytest_files(text)
        )
        # schema 是最後一個 job 時讀到檔尾
        tail = text.split("  secrets:\n", 1)[0]
        self.assertEqual(
            {"tests/test_a_db.py", "tests/test_b_db.py", "tests/test_c.py"}, _ci_schema_pytest_files(tail)
        )
        with self.assertRaises(AssertionError):
            _ci_schema_pytest_files(text.replace("  schema:", "  schemas:"))
