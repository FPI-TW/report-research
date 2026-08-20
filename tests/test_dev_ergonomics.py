# tests/test_dev_ergonomics.py
"""開發捷徑與依賴衛生的守門。全部靜態，不需要 DB 或模型。

兩件事：

1. **`SKIP_WARMUP` / `make serve-dev`**。`make serve` 刻意沒有 `--reload`（生產是對
   的），但 BGE-M3 ＋ reranker 冷載入合計約一分鐘，所以改一行 Python 就要再等一次
   ——那正是「直接在生產機上改檔然後懶得重啟」的溫床（`docs/production_resilience.md`
   已記錄過一次 unit 漂移）。這裡釘住捷徑存在、且**預設仍會暖機**。

2. **`pytest-asyncio` 不得回來**。它曾在 dev 依賴裡但全 repo 零使用（42 個測試檔走
   stdlib 的 `unittest.IsolatedAsyncioTestCase`）。留著它會讓下一個人以為「這個 repo
   的 async 測試用 pytest-asyncio」而混用兩種風格，而兩者對 event loop 的處理不同。
"""
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


class SkipWarmupTests(unittest.TestCase):
    def setUp(self):
        self.src = (REPO_ROOT / "web" / "server.py").read_text(encoding="utf-8")

    def test_warmup_can_be_skipped(self):
        self.assertIn('os.environ.get("SKIP_WARMUP")', self.src)

    def test_default_is_to_warm_up(self):
        """必須是「等於 '1' 才跳過」，不能是「有設就跳過」。

        `SKIP_WARMUP=0` 或 `SKIP_WARMUP=false` 照字面讀是「不要跳過」；
        用 truthiness 判斷會讓那兩種寫法反而跳過暖機，而症狀是生產第一個查詢
        突然多花一分鐘。
        """
        self.assertRegex(
            self.src, r'os\.environ\.get\("SKIP_WARMUP"\)\s*==\s*"1"'
        )

    def test_skip_is_logged(self):
        """跳過暖機必須留一行——否則「今天怎麼這麼慢」查無可查。"""
        idx = self.src.index('os.environ.get("SKIP_WARMUP")')
        window = self.src[idx : idx + 400]
        self.assertIn("logger.warning", window)

    def test_not_read_through_config(self):
        """刻意走 os.environ 而非 app/config.py。

        這是「這次啟動」的一次性選擇，不是部署設定。寫進 `.env` 會讓某次 debug 的
        旗標永久留在生產機上——那正是 sync unit 漂掉的方式。
        """
        cfg = (REPO_ROOT / "app" / "config.py").read_text(encoding="utf-8")
        self.assertNotIn("SKIP_WARMUP", cfg)
        env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertNotIn("SKIP_WARMUP", env_example)


class ServeDevTargetTests(unittest.TestCase):
    def setUp(self):
        self.mk = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    def test_target_exists_and_is_self_documenting(self):
        self.assertIn("\nserve-dev:", self.mk)
        line = next(ln for ln in self.mk.splitlines() if ln.startswith("serve-dev:"))
        self.assertIn("## ", line, "缺少 make help 用的 ## 說明")

    def test_dev_target_reloads_and_skips_warmup(self):
        body = self._recipe("serve-dev")
        self.assertIn("--reload", body)
        self.assertIn("SKIP_WARMUP=1", body)

    def test_production_target_does_neither(self):
        """`serve` 不得長出 --reload 或 SKIP_WARMUP——那是這兩個 target 分開的全部理由。"""
        body = self._recipe("serve")
        self.assertNotIn("--reload", body)
        self.assertNotIn("SKIP_WARMUP", body)

    def test_dev_target_binds_loopback_only(self):
        """開發模式跳過暖機又開 reload，不該讓同網段的人連進來當成正式站。"""
        self.assertIn("127.0.0.1", self._recipe("serve-dev"))

    def _recipe(self, target: str) -> str:
        """取某個 target 的 recipe 行（tab 開頭的那幾行）。"""
        lines = self.mk.splitlines()
        i = next(n for n, ln in enumerate(lines) if ln.startswith(f"{target}:"))
        out = []
        for ln in lines[i + 1 :]:
            if ln.startswith("\t"):
                out.append(ln)
            elif ln.strip() == "" or ln.startswith("#"):
                continue
            else:
                break
        return "\n".join(out)


class ServePreviewTargetTests(unittest.TestCase):
    """`make serve-preview`（免登入版面預覽）的守門。

    最關鍵的一條是**埠不得共用**：8097 是 systemd 那支對外服務的埠（cloudflared →
    nginx → 8097）。在它上面開 DEV_NO_AUTH 等於把對外站台的登入關掉，而
    `web/dev_mode.py` 的代理／loopback 判定在「nginx 與 app 同機直連」的部署下是
    最後一道、不是唯一一道防線。
    """

    def setUp(self):
        self.mk = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        self.helper = ServeDevTargetTests()
        self.helper.mk = self.mk

    def test_target_exists_and_is_self_documenting(self):
        self.assertIn("\nserve-preview:", self.mk)
        line = next(ln for ln in self.mk.splitlines() if ln.startswith("serve-preview:"))
        self.assertIn("## ", line, "缺少 make help 用的 ## 說明")

    def test_preview_enables_dev_no_auth_and_skips_warmup(self):
        body = self.helper._recipe("serve-preview")
        self.assertIn("DEV_NO_AUTH=1", body)
        self.assertIn("SKIP_WARMUP=1", body)

    def test_preview_uses_its_own_port(self):
        body = self.helper._recipe("serve-preview")
        self.assertIn("$(PREVIEW_PORT)", body)
        self.assertNotIn("$(PORT)", body)
        self.assertIn("PREVIEW_PORT ?=", self.mk)

    def test_preview_binds_loopback_only(self):
        self.assertIn("127.0.0.1", self.helper._recipe("serve-preview"))

    def test_serving_targets_never_disable_auth(self):
        """`serve` 與 `serve-dev` 都不得長出 DEV_NO_AUTH。"""
        for target in ("serve", "serve-dev"):
            with self.subTest(target=target):
                self.assertNotIn("DEV_NO_AUTH", self.helper._recipe(target))


class PytestAsyncioStaysOutTests(unittest.TestCase):
    def test_not_declared_as_a_dependency(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        # 註解裡提到它是刻意的（解釋為何移除），所以只擋「宣告成依賴」那一行。
        declared = re.search(r'^\s*"pytest-asyncio', pyproject, re.MULTILINE)
        self.assertIsNone(
            declared,
            "pytest-asyncio 又被加回 dev 依賴。若真要用它，請把 42 個 "
            "IsolatedAsyncioTestCase 整批遷移，不要兩種風格並存。",
        )

    def test_no_asyncio_marker_or_mode_in_use(self):
        """反向確認移除是安全的：真的一處都沒用。

        這條同時是「遷移到 pytest-asyncio」的前置檢查——它紅了代表已經有人開始
        混用，那時上面那條擋依賴的斷言就該一起重新考慮。
        """
        # 排除本檔：這些字串就是它的搜尋條件，掃到自己是必然的假命中。
        me = Path(__file__).resolve()
        hits = []
        for root in ("tests", "eval"):
            for path in (REPO_ROOT / root).rglob("*.py"):
                if "__pycache__" in path.parts or path.resolve() == me:
                    continue
                text = path.read_text(encoding="utf-8")
                for needle in ("pytest.mark.asyncio", "asyncio_mode", "import pytest_asyncio"):
                    if needle in text:
                        hits.append(f"{path.relative_to(REPO_ROOT)}: {needle}")
        self.assertEqual(hits, [], "發現 pytest-asyncio 用法：" + ", ".join(hits))

    def test_stdlib_async_testcase_is_the_convention(self):
        """把「慣例是什麼」也釘住，讓紅的時候看得出該往哪個方向修。"""
        users = [
            p
            for p in (REPO_ROOT / "tests").rglob("*.py")
            if "__pycache__" not in p.parts
            and "IsolatedAsyncioTestCase" in p.read_text(encoding="utf-8")
        ]
        self.assertGreater(len(users), 30, "async 測試的慣例是 IsolatedAsyncioTestCase")


if __name__ == "__main__":
    unittest.main()
