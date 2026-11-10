"""prompt 注入 profile 參數後，廷豐 profile 渲染出的全文必須與改動前逐字相同。

`tests/fixtures/profile_prompts_tingfong.json` 是 profile 機制上線**之前**的程式直接印出來的全文（golden）。
系統提示差一個字，評測基準線（eval/baselines/）就不再可比，LLM 跳過名單（以 model 為鍵、不看 prompt）也會
繼續擋著已經改過 prompt 的任務。所以這份 golden 只能在「刻意改 prompt」時重生，並同步重跑評測。

另一半：換成別的 profile 時，稱謂確實換掉、而且一個「廷豐」都不剩。
"""

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import profile as P  # noqa: E402
from app.services import agentic_qa as aq  # noqa: E402
from app.services import answer as ans  # noqa: E402
from app.services import (
    overview,  # noqa: E402
    totp,  # noqa: E402
)
from app.services import query_planner as qp  # noqa: E402
from app.services import scope_router as sr  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "profile_prompts_tingfong.json"


def render_all() -> dict:
    """與產生 golden 時同一組鍵；舊常數名經模組 __getattr__ 取值，順便驗相容層。"""
    return {
        "answer.SYSTEM_PROMPT_BASE": ans.SYSTEM_PROMPT_BASE,
        "answer.NO_WEB_POLICY": ans.NO_WEB_POLICY,
        "answer.WEB_POLICY": ans.WEB_POLICY,
        "answer.SYSTEM_PROMPT": ans.SYSTEM_PROMPT,
        "answer.ask_system_prompt(False)": ans.ask_system_prompt(False),
        "answer.ask_system_prompt(True)": ans.ask_system_prompt(True),
        "answer.TIME_SENSITIVE_WEB_SYSTEM_PROMPT": ans.TIME_SENSITIVE_WEB_SYSTEM_PROMPT,
        "answer.OFF_TOPIC_MESSAGE": ans.OFF_TOPIC_MESSAGE,
        "answer.OFF_TOPIC_MESSAGE_EN": ans.OFF_TOPIC_MESSAGE_EN,
        "answer.OFF_TOPIC_MESSAGES": list(ans.OFF_TOPIC_MESSAGES),
        "answer.NOTICE_MESSAGES": list(ans.NOTICE_MESSAGES),
        "answer.off_topic_message('zh')": ans.off_topic_message("zh"),
        "answer.off_topic_message('en')": ans.off_topic_message("en"),
        "overview.OVERVIEW_SYSTEM_PROMPT": overview.OVERVIEW_SYSTEM_PROMPT,
        "scope_router.ROUTE_SYSTEM_PROMPT": sr.ROUTE_SYSTEM_PROMPT,
        "scope_router.CONDENSE_ROUTE_SYSTEM_PROMPT": sr.CONDENSE_ROUTE_SYSTEM_PROMPT,
        "query_planner._build_qa_prompt(q, 4)": list(qp._build_qa_prompt("台積電與聯電的比較", 4)),
        "query_planner._build_qa_prompt(q, 2)": list(qp._build_qa_prompt("台積電與聯電的比較", 2)),
        "agentic_qa._build_evaluation_prompt(q, [], '')": list(aq._build_evaluation_prompt("台積電展望", [], "")),
    }


class TingfongGoldenTests(unittest.TestCase):
    def test_rendered_prompts_match_golden_verbatim(self):
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        rendered = render_all()
        self.assertEqual(set(rendered), set(golden))
        for key, expected in golden.items():
            with self.subTest(key=key):
                self.assertEqual(rendered[key], expected)

    def test_module_functions_match_compat_names(self):
        self.assertEqual(ans.system_prompt_base(), ans.SYSTEM_PROMPT_BASE)
        self.assertEqual(ans.off_topic_messages(), ans.OFF_TOPIC_MESSAGES)
        self.assertEqual(ans.notice_messages(), ans.NOTICE_MESSAGES)
        self.assertEqual(sr.route_system_prompt(), sr.ROUTE_SYSTEM_PROMPT)
        self.assertEqual(overview.overview_system_prompt(), overview.OVERVIEW_SYSTEM_PROMPT)

    def test_unknown_attribute_still_raises(self):
        for mod in (ans, sr, overview):
            with self.subTest(mod=mod.__name__), self.assertRaises(AttributeError):
                mod.NO_SUCH_PROMPT  # noqa: B018

    def test_totp_issuer_unchanged(self):
        self.assertEqual(totp.current_issuer(), "廷豐智能研報")


class OtherProfileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "acme"
        root.mkdir()
        (root / "profile.toml").write_text(textwrap.dedent("""
            [institution]
            name = "艾克米"
            product_name = "艾克米研究平台"
            short_name = "艾克米研報"
            [prompt]
            system_name = "艾克米研報"
            system_name_en = "ACME Research"
            [security]
            totp_issuer = "ACME Research"
        """), encoding="utf-8")
        self._env = patch.dict(os.environ, {"PROFILE_DIR": str(root)})
        self._env.start()
        P.reset_profile_cache()

    def tearDown(self):
        self._env.stop()
        P.reset_profile_cache()
        self._tmp.cleanup()

    def test_every_prompt_uses_the_profile_name(self):
        rendered = render_all()
        flat = json.dumps(rendered, ensure_ascii=False)
        self.assertNotIn("廷豐", flat)
        for key in ("answer.SYSTEM_PROMPT_BASE", "answer.TIME_SENSITIVE_WEB_SYSTEM_PROMPT",
                    "overview.OVERVIEW_SYSTEM_PROMPT", "scope_router.ROUTE_SYSTEM_PROMPT",
                    "scope_router.CONDENSE_ROUTE_SYSTEM_PROMPT"):
            with self.subTest(key=key):
                self.assertTrue(rendered[key].startswith("你是「艾克米研報」"), rendered[key][:30])
        self.assertTrue(rendered["query_planner._build_qa_prompt(q, 4)"][0].startswith("你是「艾克米研報」"))
        self.assertTrue(rendered["agentic_qa._build_evaluation_prompt(q, [], '')"][0].startswith("你是「艾克米研報」"))
        self.assertIn("This is ACME Research's", ans.off_topic_message("en"))
        self.assertIn("這裡是艾克米研報的投資研究問答", ans.off_topic_message("zh"))

    def test_only_the_name_changes(self):
        """稱謂以外的規則逐字不變：prompt 不開放覆寫，只注入參數。"""
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        rendered = render_all()
        for key, expected in golden.items():
            with self.subTest(key=key):
                self.assertEqual(
                    json.dumps(rendered[key], ensure_ascii=False).replace("ACME Research", "廷豐研報")
                    .replace("艾克米研報", "廷豐研報"),
                    json.dumps(expected, ensure_ascii=False),
                )

    def test_totp_issuer_from_profile(self):
        self.assertEqual(totp.current_issuer(), "ACME Research")
        self.assertIn("issuer=ACME%20Research", totp.otpauth_uri("ABCDEFGH", "alice"))


if __name__ == "__main__":
    unittest.main()
