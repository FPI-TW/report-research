"""frontend/src/lib/generated/adminApi.ts 必須等於由目前 OpenAPI 重新產生的結果。

後端改了 /api/admin/* 的 pydantic model（欄位、Literal、路徑）卻沒重新產生，前端拿到的就是過期的
契約——zod 會把新欄位靜默丟掉，或把合法的新 enum 值當成解析錯誤。紅了就跑：
    uv run python scripts/gen_admin_client.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import gen_admin_client as gen  # noqa: E402


class GeneratedAdminClientTests(unittest.TestCase):
    def test_generated_file_is_up_to_date(self):
        expected = gen.generate(gen.current_spec())
        actual = gen.OUTPUT.read_text(encoding="utf-8") if gen.OUTPUT.exists() else ""
        self.assertEqual(actual, expected, "adminApi.ts 過期：請執行 uv run python scripts/gen_admin_client.py")

    def test_every_admin_endpoint_is_in_the_client(self):
        spec = gen.current_spec()
        text = gen.OUTPUT.read_text(encoding="utf-8")
        for path, method, _op in gen.collect_operations(spec):
            self.assertIn(f"{method.upper()} {path}", text)

    def test_unsupported_schema_fails_loudly(self):
        with self.assertRaises(gen.Unsupported):
            gen.zod({"type": "mystery"})
        with self.assertRaises(gen.Unsupported):
            gen.zod({"enum": [1, 2]})

    def test_query_param_types(self):
        self.assertEqual(gen.ts_query_type({"anyOf": [{"type": "boolean"}, {"type": "null"}]}), "boolean | null")
        self.assertEqual(gen.ts_query_type({"enum": ["pending", "all"], "type": "string"}), "'pending' | 'all'")
        self.assertEqual(gen.ts_query_type({"type": "integer"}), "number")
        with self.assertRaises(gen.Unsupported):
            gen.ts_query_type({"type": "object"})

    def test_nullable_and_refs(self):
        self.assertEqual(gen.zod({"anyOf": [{"type": "string"}, {"type": "null"}]}), "z.string().nullable()")
        self.assertEqual(gen.zod({"$ref": "#/components/schemas/User-Input"}), "UserInputSchema")


if __name__ == "__main__":
    unittest.main()
