"""`docs/EXTERNAL_API.md` 與對外 API 程式的對帳。

這份文件是交給 API 用戶端的說明，後台「API 用戶端」頁直接渲染它，「試打」分頁的表單也從它的
參數表解析出來（`frontend/src/features/admin/apiClients/apiDoc.ts`）。所以它不只是文件：寫錯參數名，
試打送出的請求就是錯的。這裡逐項釘住：

- `/external/` 下的每條路由都有一節 `### <METHOD> <path>`，文件也沒有多出不存在的端點；
- 每一節的參數表恰好是該路由的 query／path 參數，「需要權限」寫的是路由實際要的 scope；
- 每把金鑰可設定的 scope（`KEY_SCOPES`）都有寫；
- 對外路由與認證會丟出的每個錯誤 `code` 都在錯誤表；
- 回應裡的每個欄位都出現在該端點那一節。

紅了是改文件（或程式），不是放寬這支測試。
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from fastapi.routing import APIRoute

from app.services.api_clients import KEY_SCOPES
from web.routers import external

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / "docs" / "EXTERNAL_API.md"
SOURCES = (REPO_ROOT / "web" / "routers" / "external.py", REPO_ROOT / "web" / "external_auth.py")

# 不經 AppError、由框架或錯誤處理層統一給的 code（web/errors.py）：對外端點一樣會收到。
FRAMEWORK_CODES = {"validation_error"}

# 每個端點的回應由哪些函式組出來：handler 本身，加上它委派組欄位的輔助函式。
# 新增對外端點時在這裡登記，回應欄位的對帳才涵蓋得到它。
RESPONSE_BUILDERS = {
    "external_search": ("external_search", "_report_payload"),
    "external_report_file_url": ("external_report_file_url",),
}

_HEADING = re.compile(r"^### (GET|POST|PUT|PATCH|DELETE) (/external/\S+)\s*$", re.M)
_FIELD_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _sections(doc: str) -> dict[tuple[str, str], str]:
    """{(METHOD, path): 那一節的內文}；一節到下一個 `##`／`###` 標題為止。"""
    out: dict[tuple[str, str], str] = {}
    for m in _HEADING.finditer(doc):
        rest = doc[m.end():]
        end = re.search(r"^#{2,3} ", rest, re.M)
        out[(m.group(1), m.group(2))] = rest[: end.start()] if end else rest
    return out


def _param_table(section: str) -> list[str]:
    """節內參數表（表頭第一欄是「參數」）第一欄的名稱。"""
    lines = section.splitlines()
    for i, line in enumerate(lines):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.lstrip().startswith("|") and cells and cells[0] == "參數":
            names = []
            for row in lines[i + 2:]:
                if not row.lstrip().startswith("|"):
                    break
                names.append(row.strip().strip("|").split("|")[0].strip().strip("`"))
            return names
    return []


def _routes() -> list[APIRoute]:
    return [r for r in external.router.routes if isinstance(r, APIRoute) and r.path.startswith("/external/")]


def _scope_of(route: APIRoute) -> str | None:
    for dep in route.dependant.dependencies:
        scope = getattr(dep.call, "api_scope", None)
        if scope:
            return scope
    return None


def _functions() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(SOURCES[0].read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _response_keys(fn: ast.AST) -> set[str]:
    """函式裡組回應用的鍵：dict 字面值的字串鍵、`x["k"] = …` 的鍵、`.update(k=…)` 的關鍵字。

    只收小寫識別字形狀的鍵（排除 `Cache-Control` 這類標頭）。
    """
    keys: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            keys.update(k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str))
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                keys.add(node.slice.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update":
            keys.update(k.arg for k in node.keywords if k.arg)
    return {k for k in keys if _FIELD_KEY.match(k)}


def _raised_codes() -> set[str]:
    """`AppError(status, "code", …)` 與 `_unauthorized("code", …)` 的 code 字面值。"""
    codes: set[str] = set()
    for path in SOURCES:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            idx = {"AppError": 1, "_unauthorized": 0}.get(node.func.id)
            if idx is None or len(node.args) <= idx:
                continue
            arg = node.args[idx]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                codes.add(arg.value)
    return codes


def _error_table_codes(doc: str) -> set[str]:
    m = re.search(r"^## 錯誤\s*$(.*?)(?=^## |\Z)", doc, re.M | re.S)
    assert m, "文件缺「## 錯誤」一節"
    return set(re.findall(r"^\|\s*\d{3}\s*\|\s*`([a-z_]+)`", m.group(1), re.M))


class ExternalApiDocTests(unittest.TestCase):
    def test_every_route_has_a_section_and_no_extra_ones(self):
        documented = set(_sections(_doc()))
        declared = {(m, r.path) for r in _routes() for m in r.methods}
        self.assertTrue(declared, "找不到任何 /external/ 路由，掃描失效")
        self.assertEqual(declared - documented, set(), "對外端點沒寫進 docs/EXTERNAL_API.md（### <METHOD> <path>）")
        self.assertEqual(documented - declared, set(), "docs/EXTERNAL_API.md 寫了不存在的端點")

    def test_param_tables_match_route_params(self):
        sections = _sections(_doc())
        for route in _routes():
            for method in route.methods:
                with self.subTest(route=route.path):
                    wanted = sorted(p.alias for p in route.dependant.query_params + route.dependant.path_params)
                    got = sorted(_param_table(sections[(method, route.path)]))
                    self.assertEqual(got, wanted, "參數表與路由的 query／path 參數不一致")

    def test_required_scope_is_stated(self):
        sections = _sections(_doc())
        for route in _routes():
            scope = _scope_of(route)
            self.assertIsNotNone(scope, f"{route.path} 沒掛 require_api_client")
            for method in route.methods:
                with self.subTest(route=route.path):
                    self.assertIn(f"需要權限：`{scope}`", sections[(method, route.path)])

    def test_every_key_scope_is_described(self):
        doc = _doc()
        for scope in KEY_SCOPES:
            self.assertIn(f"`{scope}`", doc)

    def test_every_error_code_is_in_the_error_table(self):
        raised = _raised_codes()
        self.assertIn("api_key_invalid", raised, "錯誤碼掃描失效")
        missing = (raised | FRAMEWORK_CODES) - _error_table_codes(_doc())
        self.assertEqual(missing, set(), "錯誤表缺這些 code")

    def test_response_fields_are_described(self):
        functions = _functions()
        sections = _sections(_doc())
        for route in _routes():
            name = route.endpoint.__name__
            self.assertIn(name, RESPONSE_BUILDERS, f"新的對外端點 {name} 要登記在 RESPONSE_BUILDERS")
            keys = set().union(*(_response_keys(functions[f]) for f in RESPONSE_BUILDERS[name]))
            self.assertTrue(keys, f"{name} 掃不到回應欄位")
            for method in route.methods:
                section = sections[(method, route.path)]
                for key in sorted(keys):
                    with self.subTest(route=route.path, field=key):
                        self.assertRegex(section, rf"[`.]{re.escape(key)}`", "回應欄位沒寫進該端點那一節")

    def test_doc_never_contains_a_real_key_shape(self):
        # 文件要交給外部：範例一律用佔位字，不放形似真金鑰的字串。
        self.assertNotRegex(_doc(), r"rmk_[0-9a-f]{8}_[A-Za-z0-9_-]{43}")


if __name__ == "__main__":
    unittest.main()
