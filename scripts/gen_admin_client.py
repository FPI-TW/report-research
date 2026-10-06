#!/usr/bin/env python3
"""由 FastAPI 的 OpenAPI 產生管理 API 的 TypeScript client（zod schema＋型別＋呼叫函式）。

用法：
    uv run python scripts/gen_admin_client.py           # 重寫 frontend/src/lib/generated/adminApi.ts
    uv run python scripts/gen_admin_client.py --check   # 只比對；不一致 rc=1（測試也會比）

範圍只有 `/api/admin/*`：後端 pydantic model（`response_model`、request body）就是契約，前端不再手抄
一份 zod——兩邊不一致時這裡重新產生、diff 會出現在 PR 裡。其他 API 的 zod 鏡像維持手寫慣例。

刻意自己寫而不引入 npm 產生器：只需要支援 pydantic 實際產出的那一小組 JSON Schema（object、
array、enum、nullable、$ref、dict），而 npm 相依會進 frontend/package-lock.json 與授權守門。
遇到不支援的型別直接失敗，不默默產生 `z.unknown()`。

輸出是決定性的（schema 依相依順序＋名稱排序、端點依路徑與方法排序），重跑不會產生 diff。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

OUTPUT = REPO_ROOT / "frontend" / "src" / "lib" / "generated" / "adminApi.ts"
PATH_PREFIX = "/api/admin/"
METHOD_ORDER = ("get", "post", "put", "patch", "delete")
BODY_METHODS = {"post": "POST", "put": "PUT", "patch": "PATCH"}

HEADER = """\
// 自動產生，請勿手改。來源：FastAPI OpenAPI 的 /api/admin/*（後端 pydantic model 即契約）。
// 重新產生：uv run python scripts/gen_admin_client.py（tests/test_admin_client_generated.py 檢查是否最新）
import { z } from 'zod'
import { jsonBody, requestJSON } from '../api'
"""


class Unsupported(ValueError):
    pass


def ts_name(name: str) -> str:
    """OpenAPI component 名稱 → TS 識別字（pydantic 的 `X-Input` 之類變成 `XInput`）。"""
    return re.sub(r"[^0-9A-Za-z_]", "", name)


def camel(words: str) -> str:
    parts = re.findall(r"[0-9A-Za-z]+", words)
    if not parts:
        raise Unsupported(f"無法從 {words!r} 產生函式名稱")
    return parts[0].lower() + "".join(p[:1].upper() + p[1:].lower() for p in parts[1:])


def ts_query_type(schema: dict) -> str:
    """query 參數的 TS 型別（全部可省略；可為 null 的 schema 也接受 null，qs() 會略過）。"""
    options = schema.get("anyOf", [schema])
    non_null = [o for o in options if o.get("type") != "null"]
    nullable = len(non_null) != len(options)
    types = []
    for o in non_null:
        if "enum" in o:
            types.extend(repr_ts(v) for v in o["enum"])
        elif o.get("type") in ("integer", "number"):
            types.append("number")
        elif o.get("type") == "boolean":
            types.append("boolean")
        elif o.get("type") == "string":
            types.append("string")
        else:
            raise Unsupported(f"不支援的 query 參數型別：{schema}")
    if nullable:
        types.append("null")
    return " | ".join(dict.fromkeys(types))


def ref_name(ref: str) -> str:
    return ts_name(ref.rsplit("/", 1)[-1]) + "Schema"


def zod(schema: dict) -> str:
    if "$ref" in schema:
        return ref_name(schema["$ref"])
    if "anyOf" in schema:
        options = schema["anyOf"]
        non_null = [o for o in options if o.get("type") != "null"]
        nullable = len(non_null) != len(options)
        inner = zod(non_null[0]) if len(non_null) == 1 else f"z.union([{', '.join(zod(o) for o in non_null)}])"
        return f"{inner}.nullable()" if nullable else inner
    if "enum" in schema:
        values = schema["enum"]
        if not all(isinstance(v, str) for v in values):
            raise Unsupported(f"只支援字串 enum：{values}")
        return "z.enum([" + ", ".join(repr_ts(v) for v in values) + "])"
    if "const" in schema:
        return f"z.literal({repr_ts(schema['const'])})"
    kind = schema.get("type")
    if kind == "string":
        return "z.string()"
    if kind == "integer":
        return "z.number().int()"
    if kind == "number":
        return "z.number()"
    if kind == "boolean":
        return "z.boolean()"
    if kind == "null":
        return "z.null()"
    if kind == "array":
        return f"z.array({zod(schema.get('items', {}))})"
    if kind == "object":
        props = schema.get("properties")
        if not props:
            return "z.record(z.string(), z.unknown())"
        return object_body(schema)
    raise Unsupported(f"不支援的 JSON Schema：{schema}")


def object_body(schema: dict) -> str:
    required = set(schema.get("required", ()))
    lines = []
    for prop, sub in schema.get("properties", {}).items():
        expr = zod(sub)
        if prop not in required:
            expr += ".optional()"
        key = prop if re.fullmatch(r"[A-Za-z_][0-9A-Za-z_]*", prop) else repr_ts(prop)
        lines.append(f"  {key}: {expr},")
    return "z.object({\n" + "\n".join(lines) + "\n})"


def repr_ts(value) -> str:
    if isinstance(value, str):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def refs_in(schema) -> set[str]:
    found: set[str] = set()
    if isinstance(schema, dict):
        if "$ref" in schema:
            found.add(schema["$ref"].rsplit("/", 1)[-1])
        for v in schema.values():
            found |= refs_in(v)
    elif isinstance(schema, list):
        for v in schema:
            found |= refs_in(v)
    return found


def collect_operations(spec: dict) -> list[tuple[str, str, dict]]:
    ops = []
    for path in sorted(p for p in spec.get("paths", {}) if p.startswith(PATH_PREFIX)):
        item = spec["paths"][path]
        for method in METHOD_ORDER:
            if method in item:
                ops.append((path, method, item[method]))
    return ops


def needed_components(spec: dict, ops) -> list[str]:
    components = spec.get("components", {}).get("schemas", {})
    pending = set()
    for _path, _method, op in ops:
        pending |= refs_in(op.get("requestBody", {}))
        for code, resp in op.get("responses", {}).items():
            if str(code).startswith("2"):
                pending |= refs_in(resp)
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        pending |= refs_in(components[name]) - seen
    # 相依順序：被引用的先輸出；同層依名稱排序，結果決定性。
    ordered: list[str] = []
    remaining = sorted(seen)
    while remaining:
        progressed = False
        for name in list(remaining):
            if refs_in(components[name]) - set(ordered) - {name} <= set():
                ordered.append(name)
                remaining.remove(name)
                progressed = True
        if not progressed:
            raise Unsupported(f"schema 有循環引用：{remaining}")
    return ordered


def response_schema(op: dict) -> dict | None:
    for code in sorted(op.get("responses", {})):
        if str(code).startswith("2"):
            content = op["responses"][code].get("content", {})
            if "application/json" in content:
                return content["application/json"].get("schema")
    return None


def build_operation(path: str, method: str, op: dict) -> str:
    name = camel(op.get("summary") or op["operationId"])
    resp = response_schema(op)
    if resp is None:
        raise Unsupported(f"{method.upper()} {path} 沒有 JSON 回應 schema（請設 response_model）")
    resp_zod = zod(resp)
    params = op.get("parameters", [])
    path_params = [p for p in params if p.get("in") == "path"]
    query_params = [p for p in params if p.get("in") == "query"]
    args: list[str] = []
    for p in path_params:
        args.append(f"{camel(p['name'])}: string")
    body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
    if body is not None:
        args.append(f"body: z.input<typeof {zod(body)}>")
    if query_params:
        fields = "; ".join(f"{p['name']}?: {ts_query_type(p.get('schema', {}))}" for p in query_params)
        args.append(f"query: {{ {fields} }} = {{}}")
    url = path
    for p in path_params:
        url = url.replace("{" + p["name"] + "}", "${encodeURIComponent(" + camel(p["name"]) + ")}")
    url_expr = f"`{url}${{qs(query)}}`" if query_params else (f"`{url}`" if path_params else repr_ts(url))
    if method == "get":
        init = "{ cache: 'no-store' }"
    elif method in BODY_METHODS:
        init = f"jsonBody('{BODY_METHODS[method]}'{', body' if body is not None else ''})"
    else:
        raise Unsupported(f"不支援的方法 {method}")
    summary = (op.get("summary") or "").strip()
    return (f"  /** {method.upper()} {path}{(' — ' + summary) if summary else ''} */\n"
            f"  {name}: ({', '.join(args)}) => requestJSON({url_expr}, {resp_zod}, {init}),")


def generate(spec: dict) -> str:
    ops = collect_operations(spec)
    if not ops:
        raise Unsupported(f"OpenAPI 裡沒有 {PATH_PREFIX} 端點")
    components = spec.get("components", {}).get("schemas", {})
    out = [HEADER]
    for name in needed_components(spec, ops):
        schema = components[name]
        expr = object_body(schema) if schema.get("type") == "object" and schema.get("properties") else zod(schema)
        out.append(f"export const {ts_name(name)}Schema = {expr}\n"
                   f"export type {ts_name(name)} = z.infer<typeof {ts_name(name)}Schema>\n")
    out.append("function qs(query: Record<string, string | number | boolean | null | undefined>): string {\n"
               "  const params = new URLSearchParams()\n"
               "  for (const [key, value] of Object.entries(query)) {\n"
               "    if (value !== undefined && value !== null) params.set(key, String(value))\n"
               "  }\n"
               "  const text = params.toString()\n"
               "  return text ? `?${text}` : ''\n"
               "}\n")
    names = [camel(op.get("summary") or op["operationId"]) for _p, _m, op in ops]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise Unsupported(f"函式名稱重複：{duplicates}（調整 endpoint 函式名）")
    out.append("export const adminApi = {\n" + "\n".join(build_operation(p, m, op) for p, m, op in ops) + "\n}\n")
    return "\n".join(out)


def current_spec() -> dict:
    from web.server import app

    return app.openapi()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="只比對，不寫檔")
    args = ap.parse_args(argv)
    text = generate(current_spec())
    if args.check:
        same = OUTPUT.exists() and OUTPUT.read_text(encoding="utf-8") == text
        print("adminApi.ts 已是最新" if same else f"{OUTPUT} 過期：請執行 uv run python scripts/gen_admin_client.py")
        return 0 if same else 1
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"已寫入 {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
