# tests/test_embed_threading.py
"""`app/services/embed.py` 的執行緒紀律。全部以 fake model 驗，不載入 BGE-M3。

**為什麼這支測試存在**：先前只有 `_lock` 保護模型建構，`model.encode` 完全無鎖。
每個 web 呼叫端都經 `asyncio.to_thread` 丟進預設執行緒池（`min(32, cpu+4)` 條），
而 `/api/search`／雷達／閱讀頁**完全沒有併發閘**——所以同時進 encode 的執行緒數
沒有上界。CPU-only 推論下 torch 自己還會再開 intra-op 執行緒，兩層相乘就是嚴重
超額訂閱。症狀不是錯誤，是每一條都變慢，而且量起來像「BGE-M3 本來就慢」。

真正要驗的不變量是「同時進 encode 的執行緒數不超過閘容量」，所以 fake model 的
encode 會記錄併發峰值——那是唯一能證明閘在生效的觀測量（只看「有沒有 with 語句」
是驗實作而不是驗行為）。
"""
import sys
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import embed  # noqa: E402


class _Vec(list):
    """有 `.tolist()` 的 list——`embed_texts` 對每個向量呼叫它（真實回傳是 ndarray）。

    第一版 fake 直接回 `list`，於是八條執行緒都在 encode **之後**以 AttributeError
    死掉。併發峰值仍然量到了，所以測試照樣綠——但那是「待驗的那一段跑完之後才炸」
    這種綠，不能當成通過。本專案已有多次 fake 形狀漂移的紀錄，這是同一種。
    """

    def tolist(self):
        return list(self)


class _CountingModel:
    """記錄 encode 的併發峰值。`hold` 讓每次呼叫停留足夠久以製造重疊。"""

    def __init__(self, hold=0.02):
        self.hold = hold
        self.peak = 0
        self.calls = 0
        self._live = 0
        self._lk = threading.Lock()

    def encode(self, texts, **kwargs):
        with self._lk:
            self._live += 1
            self.calls += 1
            self.peak = max(self.peak, self._live)
        try:
            # 用 Event.wait 而非 sleep：測試裡的 sleep 會被視為固定成本，
            # 而這裡只需要「停留一段可重疊的時間」。
            threading.Event().wait(self.hold)
        finally:
            with self._lk:
                self._live -= 1
        return {"dense_vecs": [_Vec([0.0] * 4) for _ in texts]}


class _Patched:
    """把 fake model 與指定容量的閘裝上去，離開時還原。"""

    def __init__(self, model, capacity):
        self.model = model
        self.capacity = capacity

    def __enter__(self):
        self._orig_model = embed._model
        self._orig_gate = embed._encode_gate
        embed._model = self.model
        embed._encode_gate = threading.Semaphore(self.capacity)
        return self.model

    def __exit__(self, *a):
        embed._model = self._orig_model
        embed._encode_gate = self._orig_gate
        return False


def _run_concurrently(n, fn):
    threads = [threading.Thread(target=fn) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert all(not t.is_alive() for t in threads), "有執行緒卡住（閘可能死鎖）"


class EncodeGateTests(unittest.TestCase):
    def test_capacity_one_serialises_encode(self):
        """核心不變量：容量 1 時併發峰值必須是 1。

        這條是整支測試的重點——沒有它，「加了一個 Semaphore」與「Semaphore 根本沒
        套在 encode 上」在測試結果上完全一樣。
        """
        model = _CountingModel()
        with _Patched(model, 1):
            _run_concurrently(8, lambda: embed.embed_texts(["x"]))
        self.assertEqual(model.calls, 8)
        self.assertEqual(model.peak, 1, f"併發峰值應為 1，實際 {model.peak}")

    def test_capacity_three_allows_three(self):
        """反向驗：閘真的是可設定的，不是把常數 1 寫死。"""
        model = _CountingModel(hold=0.05)
        with _Patched(model, 3):
            _run_concurrently(9, lambda: embed.embed_texts(["x"]))
        self.assertEqual(model.calls, 9)
        self.assertGreater(model.peak, 1, "容量 3 卻從未重疊，閘可能被寫死成 1")
        self.assertLessEqual(model.peak, 3, f"併發峰值超過容量：{model.peak}")

    def test_gate_released_on_encode_exception(self):
        """encode 拋例外必須釋放閘，否則第一次失敗就讓整個檢索永久卡住。

        `with semaphore` 本來就保證這件事，寫成測試是因為改成手動
        `acquire()`/`release()` 是很自然的「優化」，而那個版本會漏。
        """

        class Boom:
            def encode(self, texts, **kwargs):
                raise RuntimeError("模型爆了")

        with _Patched(Boom(), 1):
            for _ in range(3):
                with self.assertRaises(RuntimeError):
                    embed.embed_texts(["x"])
            # 還能取到＝前幾次都放掉了
            self.assertTrue(embed._encode_gate.acquire(timeout=1))
            embed._encode_gate.release()

    def test_empty_input_short_circuits_before_the_gate(self):
        model = _CountingModel()
        with _Patched(model, 1):
            self.assertEqual(embed.embed_texts([]), [])
        self.assertEqual(model.calls, 0)


class GateConstructionTests(unittest.TestCase):
    def test_non_positive_capacity_falls_back_to_one(self):
        """0 的語意會是「誰都不准跑」——那是永久掛住，不是「關閉限流」。"""
        import app.config as cfg

        orig = cfg.get_settings
        try:
            for raw in (0, -3):
                with self.subTest(raw=raw):
                    class _S:
                        embed_max_concurrency = raw
                        embed_torch_threads = 0

                    cfg.get_settings = lambda: _S()
                    embed.get_settings = cfg.get_settings
                    self.assertEqual(embed._make_encode_gate()._value, 1)
        finally:
            cfg.get_settings = orig
            embed.get_settings = orig


class ModelBuildIsOutsideTheGateTests(unittest.TestCase):
    """`_get_model()` 刻意在閘之外。

    首次載入要數十秒（含下載 2-4GB）。把它圈進閘裡等於讓第一個請求把後面全部擋住
    那麼久——而那正是「加鎖修好併發問題」最容易順手做錯的一步。
    """

    def test_source_calls_get_model_before_acquiring_gate(self):
        import ast
        import inspect

        src = inspect.getsource(embed.embed_texts)
        tree = ast.parse(src.strip())
        fn = tree.body[0]
        get_model_line = next(
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_get_model"
        )
        with_line = next(
            n.lineno for n in ast.walk(fn) if isinstance(n, ast.With)
        )
        self.assertLess(
            get_model_line, with_line,
            "_get_model() 必須在 with _encode_gate 之前——否則首次載入會擋住所有請求",
        )


class TokenizersParallelismTests(unittest.TestCase):
    def test_env_is_set_before_flagembedding_import(self):
        """HF tokenizers 只在首次匯入時讀 `TOKENIZERS_PARALLELISM`，之後改無效。

        所以這是**順序**契約，只有靜態檢查抓得到：真的跑一次的話，tokenizers 早就
        被別的測試匯入過了。
        """
        import inspect

        src = inspect.getsource(embed._get_model)
        i_env = src.index("TOKENIZERS_PARALLELISM")
        i_import = src.index("from FlagEmbedding import")
        self.assertLess(i_env, i_import)

    def test_uses_setdefault_not_assignment(self):
        """外部已經設了就不覆蓋——批次想開回來時 `TOKENIZERS_PARALLELISM=true` 要有效。"""
        import inspect

        self.assertIn(
            'os.environ.setdefault("TOKENIZERS_PARALLELISM"',
            inspect.getsource(embed._get_model),
        )


if __name__ == "__main__":
    unittest.main()
