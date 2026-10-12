"""前端手寫詞彙與後端唯一定義逐字對帳（讀 TypeScript 原始碼，不跑 node）。

兩組詞彙沒有產生器、也不經 OpenAPI，漂移時都是靜默的：
- `review_state`（kind／status／verification）：`web/routers/review.py` 的 `Literal` 對
  `frontend/src/lib/reviewSchemas.ts` 的 `z.enum`。前端少一個值，整份佇列回應 parse 失敗；
  多一個值，畫面就能送出後端回 422 的請求。
- 上傳狀態與 `failure_kind`：`app/services/uploads.py` 的常數對
  `frontend/src/features/admin/uploadLabels.ts` 的中文標籤、分頁籤與狀態分組。
  標籤缺鍵時畫面顯示原始代碼（不擋畫面，所以沒人發現）；分組漂移時輪詢、退回與重試按鈕
  對錯的狀態出現。

以後端為準：紅了就改前端那一側。TypeScript 端只認字面量（字串、識別字、物件與陣列字面量），
寫法換成展開、函式呼叫或樣板字串時解析會明確失敗，而不是少收幾個值照樣綠。
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from app.services import uploads

REPO_ROOT = Path(__file__).resolve().parents[1]
REVIEW_PY = REPO_ROOT / "web" / "routers" / "review.py"
REVIEW_TS = REPO_ROOT / "frontend" / "src" / "lib" / "reviewSchemas.ts"
UPLOAD_TS = REPO_ROOT / "frontend" / "src" / "features" / "admin" / "uploadLabels.ts"

_STR = r"""'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*\""""
_LINE_COMMENT = re.compile(r"^\s*//.*$", re.M)


def _unquote(lit: str) -> str:
    return ast.literal_eval(lit)


def _balanced(src: str, open_at: int) -> str:
    """`src[open_at]` 是 `[` 或 `{`；回傳到配對括號為止的內容（不含兩端），略過字串裡的括號。"""
    pairs = {"[": "]", "{": "}"}
    stack = [pairs[src[open_at]]]
    i = open_at + 1
    while i < len(src):
        ch = src[i]
        if ch in "'\"":
            m = re.compile(_STR).match(src, i)
            if not m:
                raise AssertionError(f"未閉合的字串：{src[i:i + 40]!r}")
            i = m.end()
            continue
        if ch in pairs:
            stack.append(pairs[ch])
        elif ch in "]}":
            if ch != stack.pop():
                raise AssertionError(f"括號不配對：{src[open_at:i + 1][-80:]!r}")
            if not stack:
                return src[open_at + 1:i]
        i += 1
    raise AssertionError(f"找不到配對括號：{src[open_at:open_at + 80]!r}")


def _string_list(body: str, where: str) -> list[str]:
    """`'a', 'b',` 這種純字串字面量清單；有任何別的東西就失敗。"""
    body = _LINE_COMMENT.sub("", body)
    items = re.findall(_STR, body)
    rest = re.sub(_STR, "", body)
    if rest.replace(",", "").strip():
        raise AssertionError(f"{where} 不是純字串字面量清單，無法對帳：{rest.strip()[:80]!r}")
    return [_unquote(s) for s in items]


def _ts_const_initializer(src: str, name: str, opener: str) -> str:
    """`export const NAME[: 型別] = <opener>…` 的初始值內容（`opener` 是 `[` 或 `{`）。"""
    hits = list(re.finditer(rf"^export const {re.escape(name)}\b[^=\n]*=\s*", src, re.M))
    if len(hits) != 1:
        raise AssertionError(f"預期恰好一個 `export const {name} = …`，找到 {len(hits)} 個")
    at = hits[0].end()
    if src[at:at + 1] != opener:
        raise AssertionError(f"`{name}` 的初始值不是 `{opener}` 開頭的字面量：{src[at:at + 40]!r}")
    return _balanced(src, at)


def ts_string_array(src: str, name: str) -> list[str]:
    return _string_list(_ts_const_initializer(src, name, "["), name)


def ts_object_keys(src: str, name: str) -> list[str]:
    """`{ key: '值', 'k-2': "值" }` 的鍵；值必須是字串字面量。"""
    body = _LINE_COMMENT.sub("", _ts_const_initializer(src, name, "{"))
    entry = re.compile(rf"\s*(?:([A-Za-z_$][\w$]*)|({_STR}))\s*:\s*(?:{_STR})\s*(?:,|$)")
    keys: list[str] = []
    pos = 0
    while pos < len(body):
        if not body[pos:].strip():
            break
        m = entry.match(body, pos)
        if not m:
            raise AssertionError(f"`{name}` 有無法解析的項目：{body[pos:].strip()[:80]!r}")
        keys.append(m.group(1) or _unquote(m.group(2)))
        pos = m.end()
    return keys


def ts_zod_enum(src: str, name: str) -> list[str]:
    hits = list(re.finditer(rf"^export const {re.escape(name)}\s*=\s*z\.enum\(\s*(?=\[)", src, re.M))
    if len(hits) != 1:
        raise AssertionError(f"預期恰好一個 `export const {name} = z.enum([...])`，找到 {len(hits)} 個")
    return _string_list(_balanced(src, hits[0].end()), name)


def py_literal_alias(path: Path, name: str) -> list[str]:
    """模組層 `NAME = Literal[...]` 的值（AST，不 import）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
    ]
    if len(found) != 1:
        raise AssertionError(f"{path.name} 預期恰好一個 `{name} = Literal[...]`，找到 {len(found)} 個")
    return _literal_values(found[0], f"{path.name}:{name}")


def _literal_values(node: ast.expr, where: str) -> list[str]:
    if not (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "Literal"):
        raise AssertionError(f"{where} 不是 `Literal[...]`")
    elts = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
    values = [e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    if len(values) != len(elts):
        raise AssertionError(f"{where} 的 Literal 含非字串常數")
    return values


def _assert_same_vocab(tc: unittest.TestCase, backend, frontend, what: str) -> None:
    frontend = list(frontend)
    dupes = sorted({v for v in frontend if frontend.count(v) > 1})
    tc.assertEqual([], dupes, f"{what}：前端重複列了 {dupes}")
    tc.assertEqual(
        set(),
        set(backend) - set(frontend),
        f"{what}：前端缺少後端有的值（以後端為準，請補前端）",
    )
    tc.assertEqual(
        set(),
        set(frontend) - set(backend),
        f"{what}：前端多了後端沒有的值（以後端為準，請刪前端或先改後端）",
    )


class ReviewStateVocabTests(unittest.TestCase):
    PAIRS = (
        ("ReviewKind", "reviewKindSchema"),
        ("ReviewStatus", "reviewStatusSchema"),
        ("Verification", "reviewVerificationSchema"),
    )

    def setUp(self):
        self.ts = REVIEW_TS.read_text(encoding="utf-8")

    def test_literals_match_zod_enums_verbatim(self):
        for py_name, ts_name in self.PAIRS:
            with self.subTest(py=py_name, ts=ts_name):
                backend = py_literal_alias(REVIEW_PY, py_name)
                self.assertTrue(backend, f"{py_name} 是空的")
                _assert_same_vocab(self, backend, ts_zod_enum(self.ts, ts_name), f"{py_name} ↔ {ts_name}")

    def test_backend_internal_variants_stay_in_vocab(self):
        """review.py 自己另寫的兩份（佇列的 status 篩選、問答專用的 kind）不得長出詞彙以外的值。"""
        kinds = set(py_literal_alias(REVIEW_PY, "ReviewKind"))
        self.assertLessEqual(set(py_literal_alias(REVIEW_PY, "QaReviewKind")), kinds)

        statuses = set(py_literal_alias(REVIEW_PY, "ReviewStatus"))
        tree = ast.parse(REVIEW_PY.read_text(encoding="utf-8"))
        status_params = [
            _literal_values(arg.annotation, f"參數 {arg.arg}")
            for fn in ast.walk(tree)
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
            for arg in fn.args.args + fn.args.kwonlyargs
            if arg.arg == "status" and isinstance(arg.annotation, ast.Subscript)
            and isinstance(arg.annotation.value, ast.Name) and arg.annotation.value.id == "Literal"
        ]
        self.assertTrue(status_params, "找不到佇列的 `status: Literal[...]` 篩選參數（改寫了就同步本測試）")
        for values in status_params:
            self.assertEqual(statuses | {"all"}, set(values))

    def test_parser_rejects_non_literal_enum(self):
        with self.assertRaises(AssertionError):
            ts_zod_enum("export const x = z.enum([...BASE, 'a'])\n", "x")
        self.assertEqual(["a", "b"], ts_zod_enum("export const x = z.enum([\n  'a',\n  \"b\",\n])\n", "x"))


class UploadVocabFrontendTests(unittest.TestCase):
    def setUp(self):
        self.ts = UPLOAD_TS.read_text(encoding="utf-8")

    def test_state_labels_cover_states(self):
        _assert_same_vocab(self, uploads.STATES, ts_object_keys(self.ts, "STATE_LABEL"), "STATE_LABEL 的鍵 ↔ STATES")

    def test_failure_labels_cover_failure_kinds(self):
        _assert_same_vocab(
            self, uploads.FAILURE_KINDS, ts_object_keys(self.ts, "FAILURE_LABEL"), "FAILURE_LABEL 的鍵 ↔ FAILURE_KINDS"
        )

    def test_state_groups_match_backend(self):
        for name in ("RETRYABLE_FAILURE_KINDS", "IN_FLIGHT_STATES", "REJECTABLE_STATES"):
            with self.subTest(name=name):
                _assert_same_vocab(self, getattr(uploads, name), ts_string_array(self.ts, name), name)

    def test_tabs_partition_states(self):
        """分頁籤是清單的唯一入口：每個狀態恰好落在一個分頁籤，否則那個狀態的上傳在畫面上找不到。"""
        body = _ts_const_initializer(self.ts, "UPLOAD_TABS", "[")
        named = {"IN_FLIGHT_STATES": ts_string_array(self.ts, "IN_FLIGHT_STATES")}
        seen: list[str] = []
        refs = list(re.finditer(r"\bstates:\s*", body))
        self.assertTrue(refs, "UPLOAD_TABS 找不到任何 `states:`")
        for m in refs:
            at = m.end()
            if body[at] == "[":
                seen += _string_list(_balanced(body, at), "UPLOAD_TABS 的 states")
            else:
                ident = re.match(r"[A-Za-z_$][\w$]*", body[at:])
                if ident is None:
                    self.fail(f"UPLOAD_TABS 的 states 無法解析：{body[at:at + 40]!r}")
                self.assertIn(ident.group(0), named, f"UPLOAD_TABS 引用了未對帳的常數 {ident.group(0)}")
                seen += named[ident.group(0)]
        _assert_same_vocab(self, uploads.STATES, seen, "UPLOAD_TABS 各分頁籤的 states 聯集 ↔ STATES")

    def test_parser_rejects_spread_and_computed_values(self):
        with self.assertRaises(AssertionError):
            ts_object_keys("export const X: Record<string, string> = {\n  ...BASE,\n  a: 'x',\n}\n", "X")
        with self.assertRaises(AssertionError):
            ts_object_keys("export const X = {\n  a: label('x'),\n}\n", "X")
        with self.assertRaises(AssertionError):
            ts_string_array("export const X: readonly string[] = [...A, 'b']\n", "X")
        self.assertEqual(
            ["a", "b-c", "d"],
            ts_object_keys("export const X = {\n  a: '甲（x: y）',\n  'b-c': \"{乙}\",\n  // 註解\n  d: 'z'\n}\n", "X"),
        )
