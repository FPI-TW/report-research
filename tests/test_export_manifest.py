# tests/test_export_manifest.py
"""交付清單 `deploy/EXPORT.toml` 與打包工具 `scripts/export_framework.py`。

守的是：
- 每個受版控的檔案恰好被一條規則歸類（deploy/、scripts/、ops_agent/ 沿用 `deploy/OWNERSHIP.toml`）：新檔案沒歸類
  就紅，列了不存在的也紅。
- 交付版的改寫對得上：`[markers].files` 與實際有 `export-omit` 標記的檔案兩邊相同、標記成對；每條 `[[rewrite]]`
  在該檔恰好出現一次。
- 交付版（套用改寫之後）的檔案不出現禁用字樣，例外逐檔釘行數；交付版的 Python 不 import 不進交付物的模組。
- 工具本身：標記語法、授權聲明的處理、產出的 tarball 內容與雜湊、同一個 commit 重打逐位元組相同（以暫存 git repo
  模擬，不碰本 repo）。

掃的是 git 索引（`git ls-files`）與工作樹的內容：還沒 `git add` 的新檔不在範圍內，CI 上照樣會被抓到。
交付物真正的驗證（解開後跑交付版自己的測試）在 `make export`，不在這裡。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import export_framework as ex  # noqa: E402


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT, check=True, capture_output=True).stdout
    return sorted(p for p in out.decode("utf-8").split("\0") if p and (REPO_ROOT / p).is_file())


def load() -> ex.Manifest:
    return ex.parse_manifest((REPO_ROOT / ex.MANIFEST_PATH).read_text(encoding="utf-8"),
                             (REPO_ROOT / ex.OWNERSHIP_PATH).read_text(encoding="utf-8"))


def _default_profile_dir(source: bytes) -> str:
    tree = ast.parse(source.decode("utf-8"))
    values = [n.value.value for n in tree.body if isinstance(n, ast.Assign)
              and any(getattr(t, "id", None) == "DEFAULT_PROFILE_DIR" for t in n.targets)]
    assert len(values) == 1
    return values[0]


def delivered(m: ex.Manifest) -> dict[str, bytes]:
    """交付版的內容（套用標記與改寫），以工作樹為準。"""
    out = {}
    for path in tracked():
        if ex.classify(m, path):
            out[path] = ex.transform(m, path, (REPO_ROOT / path).read_bytes())[0]
    return out


class ManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load()
        cls.files = tracked()

    def test_every_tracked_file_is_classified_and_no_stale_entries(self):
        problems = ex.manifest_problems(self.m, self.files)
        self.assertFalse(problems, "\n".join(problems))

    def test_profile_and_host_layer_are_excluded(self):
        for path in self.files:
            if path.startswith("profiles/tingfong/") or path in self.m.host:
                self.assertFalse(ex.classify(self.m, path), path)
        for path in ("app/profile.py", "profiles/demo/profile.toml", "deploy/container/Dockerfile", "README.md"):
            self.assertTrue(ex.classify(self.m, path), path)
        for path in (ex.MANIFEST_PATH, ex.OWNERSHIP_PATH, "scripts/export_framework.py",
                     "tests/test_export_manifest.py"):
            self.assertFalse(ex.classify(self.m, path), f"{path} 是源頭 repo 專用，不隨交付物")

    def test_license_is_always_delivered(self):
        for name in ex.LICENSE_NAMES:
            self.assertTrue(ex.classify(self.m, name))

    def test_marker_files_match_actual_markers(self):
        with_markers = set()
        for path in self.files:
            if not ex.classify(self.m, path):
                continue                   # 不進交付物的檔案（例如打包工具自己）不套標記
            try:
                text = (REPO_ROOT / path).read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if ex.has_marker(text):
                with_markers.add(path)
        self.assertEqual(sorted(with_markers - self.m.marker_files), [],
                         "有 export-omit 標記但沒列在 [markers].files")
        self.assertEqual(sorted(self.m.marker_files - with_markers), [],
                         "列在 [markers].files 卻沒有標記")
        for path in sorted(self.m.marker_files):
            with self.subTest(path=path):
                ex.strip_markers((REPO_ROOT / path).read_text(encoding="utf-8"), path)

    def test_rewrites_apply_exactly_once(self):
        for rw in self.m.rewrites:
            with self.subTest(file=rw.file, old=rw.old):
                text = (REPO_ROOT / rw.file).read_text(encoding="utf-8")
                self.assertEqual(text.count(rw.old), 1)
                self.assertTrue(rw.reason.strip())

    def test_delivered_content_has_no_forbidden_words(self):
        pat = self.m.forbidden_re
        unexpected = []
        for path, data in delivered(self.m).items():
            if path in ex.LICENSE_NAMES:
                continue
            self.assertIsNone(pat.search(path), f"路徑含禁用字樣：{path}")
            try:
                lines = [f"{path}:{i}: {ln.strip()[:120]}"
                         for i, ln in enumerate(data.decode("utf-8").splitlines(), 1) if pat.search(ln)]
            except UnicodeDecodeError:
                low = data.lower()
                lines = [f"{path}: 二進位檔" for w in self.m.forbidden if w.encode("utf-8").lower() in low]
            allowed = self.m.allow.get(path, (0, ""))[0]
            if len(lines) != allowed:
                unexpected.append(f"{path}（允許 {allowed} 行、實際 {len(lines)} 行）\n    " + "\n    ".join(lines))
        self.assertFalse(unexpected, "交付版出現禁用字樣：改寫成框架用語、加 export-omit 標記或排除\n"
                         + "\n".join(unexpected))

    def test_delivery_has_no_default_profile(self):
        """交付版的 `DEFAULT_PROFILE_DIR` 是空字串（PROFILE_DIR 必填）；源頭 repo 的預設照舊是可載入的 profile。"""
        files = delivered(self.m)
        for path in ("app/profile.py", "eval/profile_eval.py"):
            with self.subTest(path=path):
                self.assertEqual(_default_profile_dir(files[path]), "")
                source = _default_profile_dir((REPO_ROOT / path).read_bytes())
                self.assertTrue((REPO_ROOT / source / "profile.toml").is_file(), f"{path} 的源頭預設不存在")
        conftest = files["tests/conftest.py"].decode("utf-8")
        self.assertIn('os.environ["PROFILE_DIR"] = "tests/fixtures/profile_framework"', conftest)
        self.assertIn("tests/fixtures/profile_framework/profile.toml", files)

    def test_delivered_python_does_not_import_excluded_modules(self):
        files = delivered(self.m)
        modules = {}
        for path in self.files:
            if path.endswith(".py"):
                mod = path[:-3].replace("/", ".").removesuffix(".__init__")
                modules[mod] = path
        test_helpers = {Path(p).stem: p for p in self.files if p.startswith("tests/") and p.endswith(".py")}
        offenders = []
        for path, data in files.items():
            if not path.endswith(".py"):
                continue
            for node in ast.walk(ast.parse(data.decode("utf-8"))):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
                for name in names:
                    target = modules.get(name)
                    if target is None and path.startswith("tests/") and "." not in name:
                        target = test_helpers.get(name)
                    if target is not None and target not in files:
                        offenders.append(f"{path} → {name}（{target} 不進交付物）")
        self.assertFalse(offenders, "交付版的程式 import 了不進交付物的模組：\n" + "\n".join(sorted(set(offenders))))

    def test_dockerfile_sources_are_delivered(self):
        files = delivered(self.m)
        dirs = {str(Path(p).parent.as_posix()) for p in files}
        dirs |= {d.rsplit("/", 1)[0] for d in list(dirs) if "/" in d}
        text = files["deploy/container/Dockerfile"].decode("utf-8")
        for src in ex.dockerfile_sources(text):
            if src != ".":
                with self.subTest(src=src):
                    self.assertTrue(src.rstrip("/") in files or src.rstrip("/") in dirs, src)

    def test_compose_relative_mounts_are_delivered(self):
        files = delivered(self.m)
        dirs = {str(Path(p).parent.as_posix()) for p in files}
        dirs |= {d.rsplit("/", 1)[0] for d in list(dirs) if "/" in d}
        composes = [p for p in files if Path(p).name.startswith("docker-compose") and p.endswith(".yml")]
        self.assertTrue(composes)
        for path in composes:
            for src in ex.compose_relative_mounts(files[path].decode("utf-8")):
                target = os.path.normpath(str(Path(path).parent / src))
                with self.subTest(compose=path, mount=src):
                    self.assertTrue(target in files or target in dirs, f"{path} 掛載的 {src} 不進交付物")


class MarkerTests(unittest.TestCase):
    def test_line_and_block_markers(self):
        text = "a\nb  # export-omit\nc\n<!-- export-omit:begin -->\nd\ne\n<!-- export-omit:end -->\nf\n"
        out, removed = ex.strip_markers(text, "x")
        self.assertEqual(out, "a\nc\nf\n")
        self.assertEqual(removed, 5)

    def test_table_row_marker_before_final_pipe(self):
        out, _ = ex.strip_markers("| a | b |\n| c | d <!-- export-omit --> |\n", "x.md")
        self.assertEqual(out, "| a | b |\n")

    def test_inline_span(self):
        text = ("| a | b<!-- export-omit:begin -->、`x.md`<!-- export-omit:end --> |\n"
                "句子一。<!-- export-omit:begin -->句子二。<!-- export-omit:end -->句子三。\n"
                "<!-- export-omit:begin -->整行都是片段<!-- export-omit:end -->\n"
                "結尾\n")
        out, removed = ex.strip_markers(text, "x.md")
        self.assertEqual(out, "| a | b |\n句子一。句子三。\n結尾\n")
        self.assertEqual(removed, 1)

    def test_js_comment_marker(self):
        out, _ = ex.strip_markers("x = 1\ny = 2 // export-omit\n", "x.ts")
        self.assertEqual(out, "x = 1\n")

    def test_errors_are_loud(self):
        cases = {
            "沒有標記": "a\nb\n",
            "begin 沒有 end": "# export-omit:begin\na\n",
            "end 沒有 begin": "a\n# export-omit:end\n",
            "巢狀": "# export-omit:begin\n# export-omit:begin\n# export-omit:end\n",
            "不在行尾": "a  # export-omit 理由寫在後面\n",
            "區塊標記沒有獨立成行": "文字 <!-- export-omit:begin -->\nb\n<!-- export-omit:end -->\n",
            "行內片段沒有收尾": "a <!-- export-omit:begin --> b\n",
        }
        for name, text in cases.items():
            with self.subTest(name):
                with self.assertRaises(ex.ExportError):
                    ex.strip_markers(text, "x")

    def test_marker_outside_listed_files_is_rejected(self):
        m = ex.parse_manifest(MINI_EXPORT, MINI_OWNERSHIP)
        with self.assertRaises(ex.ExportError):
            ex.transform(m, "app/core.py", b"a\nb  # export-omit\n")

    def test_rewrite_must_match_exactly_once(self):
        rw = ex.Rewrite("f", "X", "Y", "r")
        self.assertEqual(ex.apply_rewrites("aXb", "f", [rw]), "aYb")
        for text in ("ab", "XX"):
            with self.assertRaises(ex.ExportError):
                ex.apply_rewrites(text, "f", [rw])


class ManifestValidationTests(unittest.TestCase):
    def test_rejects_overlap_wildcards_and_owned_scopes(self):
        bad = {
            "目錄巢狀": '[paths]\ninclude_dirs = ["a/", "a/b/"]\n',
            "檔案已被目錄涵蓋": '[paths]\ninclude_dirs = ["a/"]\ninclude = ["a/x.py"]\n',
            "萬用字元": '[paths]\ninclude = ["a/*.py"]\n',
            "兩邊都列": '[paths]\ninclude = ["x"]\nexclude = ["x"]\n',
            "屬 OWNERSHIP 範圍": '[paths]\ninclude = ["scripts/x.py"]\n',
            "未知 section": '[path]\ninclude = ["x"]\n',
            "改寫缺理由": '[[rewrite]]\nfile = "x"\nold = "a"\nnew = "b"\n',
        }
        for name, text in bad.items():
            with self.subTest(name):
                with self.assertRaises(ex.ExportError):
                    ex.parse_manifest(text + '[verify]\nforbidden = ["acme"]\n', MINI_OWNERSHIP)


# ── 端到端：暫存 git repo ─────────────────────────────────────────────────

MINI_OWNERSHIP = """
[framework]
files = ["scripts/tool.py"]
[tingfong_host]
files = ["scripts/host.sh", "deploy/OWNERSHIP.toml", "deploy/EXPORT.toml"]
"""

MINI_EXPORT = """
[paths]
include_dirs = ["app/"]
exclude_dirs = ["profiles/acme/"]
include = ["README.md", "app_settings.py"]
exclude = ["notes.md"]

[markers]
files = ["README.md"]

[[rewrite]]
file = "app_settings.py"
old = 'DEFAULT = "profiles/acme"'
new = 'DEFAULT = "profiles/demo"'
reason = "測試"

[verify]
forbidden = ["acme"]
"""

MINI_FILES = {
    "deploy/OWNERSHIP.toml": MINI_OWNERSHIP,
    "deploy/EXPORT.toml": MINI_EXPORT,
    "scripts/tool.py": "print('tool')\n",
    "scripts/host.sh": "echo acme\n",
    "app/__init__.py": "",
    "app/core.py": "X = 1\n",
    "app_settings.py": 'DEFAULT = "profiles/acme"\n',
    "profiles/acme/profile.toml": "name = 'acme'\n",
    "README.md": "# 框架\n\n通用段落。\n\n<!-- export-omit:begin -->\n## Acme 的部署\n<!-- export-omit:end -->\n",
    "notes.md": "acme 內部筆記\n",
}


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "commit.gpgsign=false", *args], cwd=repo, check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        self._commit(MINI_FILES, "init")

    def tearDown(self):
        self.tmp.cleanup()

    def _commit(self, files: dict[str, str], msg: str) -> str:
        for rel, text in files.items():
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", msg)
        return _git(self.repo, "rev-parse", "HEAD")

    def _build(self, license_file: Path | None = None) -> tuple[ex.Build, Path, ex.Commit]:
        commit = ex.resolve_commit("HEAD", repo=self.repo)
        b = ex.build(commit, license_file, repo=self.repo)
        out = Path(self.tmp.name) / "out"
        return b, ex.write_tarball(b, commit.timestamp, out), commit

    def _members(self, tarball: Path) -> dict[str, bytes]:
        with tarfile.open(tarball, "r:gz") as tar:
            return {ti.name.split("/", 1)[1]: tar.extractfile(ti).read() for ti in tar.getmembers() if ti.isfile()}

    def test_contents_markers_rewrites_and_metadata(self):
        b, tarball, commit = self._build()
        files = self._members(tarball)
        self.assertEqual(sorted(files), ["DELIVERY_INFO.json", "README.md", "SHA256SUMS", "app/__init__.py",
                                         "app/core.py", "app_settings.py", "scripts/tool.py"])
        self.assertNotIn(b"acme", files["README.md"].lower())
        self.assertEqual(files["app_settings.py"], b'DEFAULT = "profiles/demo"\n')
        info = json.loads(files["DELIVERY_INFO.json"])
        self.assertEqual(info["source_commit"], commit.sha)
        self.assertEqual(info["license"], None)
        self.assertEqual(info["transformed"], ["README.md", "app_settings.py"])
        sums = dict(reversed(ln.split("  ", 1)) for ln in files["SHA256SUMS"].decode().splitlines())
        self.assertEqual(set(sums), set(files) - {"SHA256SUMS"})
        for path, digest in sums.items():
            self.assertEqual(hashlib.sha256(files[path]).hexdigest(), digest, path)
        self.assertTrue(tarball.name.startswith(f"{ex.NAME_PREFIX}-") and commit.sha[:12] in tarball.name)

    def test_same_commit_gives_identical_tarball(self):
        _, first, _ = self._build()
        digest = ex.file_sha256(first)
        first.unlink()
        _, second, _ = self._build()
        self.assertEqual(ex.file_sha256(second), digest)

    def test_license_in_repo_is_delivered_and_override_wins(self):
        self._commit({"LICENSE": "Copyright (c) 2026 Example. All rights reserved.\n"}, "license")
        b, tarball, _ = self._build()
        self.assertEqual(b.license_source, "LICENSE")
        self.assertIn("LICENSE", self._members(tarball))
        other = Path(self.tmp.name) / "customer-license.txt"
        other.write_text("customer specific\n", encoding="utf-8")
        b, tarball, _ = self._build(other)
        self.assertEqual(self._members(tarball)["LICENSE"], b"customer specific\n")
        self.assertIn("--license", b.license_source)

    def test_unclassified_file_refuses_to_build(self):
        self._commit({"new_top_level.py": "x = 1\n"}, "new")
        with self.assertRaisesRegex(ex.ExportError, "未歸類"):
            self._build()

    def test_forbidden_scan_catches_leaks_and_respects_allowlist(self):
        _, tarball, _ = self._build()
        m = ex.parse_manifest(MINI_EXPORT, MINI_OWNERSHIP)
        work = Path(self.tmp.name) / "work"
        with tarfile.open(tarball, "r:gz") as tar:
            tar.extractall(work, filter="data")
        root = next(work.iterdir())
        self.assertEqual(ex.scan_forbidden(root, m), [])
        (root / "app" / "core.py").write_text("X = 'ACME'\n", encoding="utf-8")
        self.assertEqual(len(ex.scan_forbidden(root, m)), 1)
        m.allow["app/core.py"] = (1, "測試")
        self.assertEqual(ex.scan_forbidden(root, m), [])


if __name__ == "__main__":
    unittest.main()
