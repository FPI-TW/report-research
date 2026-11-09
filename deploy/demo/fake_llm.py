"""示範環境與 E2E 用的假 LLM：極小的 OpenAI 相容 Chat Completions 伺服器（只用標準函式庫）。

給 `LLM_PROVIDER=openai_compatible` 指過來（`OPENAI_COMPATIBLE_BASE_URL=http://fake-llm:8080/v1`），同時是
`openai_compatible` adapter 的實機驗證：請求走的是 `app/services/llm_http.py` 的真實 URL、header 與 body，
串流也是真的 SSE（`data: {...}` 直到 `data: [DONE]`），usage 照 OpenAI 的欄位回。

**任務以模型名分派**：一個任務一個名稱（`MODELS`），部署層以各任務旋鈕（`TAG_MODEL`、`SUMMARY_MODEL`…）把任務
指到對應的名稱，並在 `OPENAI_COMPATIBLE_MODELS` 宣告這組名稱（白名單照常是安全閘門）。請求本身不帶任務名，
從 prompt 猜任務既脆弱又會在 prompt 改版時靜默走錯，名稱分派則是決定性的。旋鈕的完整清單在
`deploy/demo/docker-compose.demo.yml`。

回應是**決定性**（同一個請求永遠同一個回答）、**格式正確**的內容：每種回應都對過該任務真實的解析器
（`tests/test_fake_llm.py`）。需要逐字引文的任務（摘錄、訊號）從 prompt 裡的研報內文挑句子，所以錨定照常成立；
問答的回答引用 prompt 裡的來源編號，並刻意夾一個不存在的編號、把引用拆在兩個 chunk 之間，讓
`CitationStreamFilter` 真的有事做（E2E 驗證串流裡看不到那個編號）。

內容一律標明是示範產出，不是任何真實模型的回答。不用於正式環境。

    python deploy/demo/fake_llm.py              # 監聽 0.0.0.0:${FAKE_LLM_PORT:-8080}
    FAKE_LLM_API_KEY=xxx python deploy/demo/fake_llm.py   # 有設就要求 Authorization: Bearer xxx

端點：`POST /v1/chat/completions`、`GET /v1/models`、`GET /healthz`。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 任務 → 模型名。名稱同時是 OPENAI_COMPATIBLE_MODELS 的宣告值（逗號串起來，見 declared_models()）。
MODELS: dict[str, str] = {
    "tag": "demo-tag",
    "summary": "demo-summary",
    "title": "demo-title",
    "takeaway": "demo-takeaway",
    "signal": "demo-signal",
    "brief": "demo-brief",
    "ask_intent": "demo-intent",
    "ask_condense": "demo-condense",
    "qa_planner": "demo-planner",
    "ask_followup": "demo-followup",
    "ask_answer": "demo-answer",
    "faithfulness": "demo-judge",
    "eval_judge": "demo-judge",
}
TASK_BY_MODEL = {}
for _task, _model in MODELS.items():
    TASK_BY_MODEL.setdefault(_model, _task)

# 問答回答刻意夾帶的不存在編號（CitationStreamFilter 要把它換成「（無效引用）」）。
INVALID_CITATION_NUMBER = 99
DEMO_MARK = "（示範產出）"


def declared_models() -> str:
    return ",".join(sorted(set(MODELS.values())))


# ── 從 prompt 取材 ───────────────────────────────────────────────────────


def _between(text: str, start: str, end: str | None = None) -> str:
    i = text.find(start)
    if i < 0:
        return ""
    i += len(start)
    if end is None:
        return text[i:]
    j = text.find(end, i)
    return text[i:] if j < 0 else text[i:j]


def report_body(prompt: str) -> str:
    """批次 prompt 裡的研報內文：標記行（標註與摘要「報告內文（前 N 字摘錄）：」、標題「報告開頭內文（…）：」、
    摘錄與訊號「研報內文：」）的下一行起，到結尾的「請依…」指示之前。找不到標記就回整段 prompt。"""
    for marker in ("研報內文：", "報告開頭內文（", "報告內文（"):
        i = prompt.find(marker)
        if i < 0:
            continue
        nl = prompt.find("\n", i)
        if nl < 0:
            continue
        rest = prompt[nl + 1:]
        j = rest.rfind("\n\n請依")
        return rest if j < 0 else rest[:j]
    return prompt


def sentences(text: str, *, lo: int = 15, hi: int = 60) -> list[str]:
    """以句號切出的句子（含句號），長度在 [lo, hi]、在原文只出現一次（逐字引文要能唯一定位）。"""
    out: list[str] = []
    for line in text.splitlines():
        for raw in re.findall(r"[^。！？]+[。！？]", line):
            s = raw.strip()
            if lo <= len(s) <= hi and text.count(s) == 1 and s not in out:
                out.append(s)
    return out


def _digest(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


def _pick(items: list[str], n: int, seed: str) -> list[str]:
    if len(items) <= n:
        return list(items)
    start = _digest(seed) % len(items)
    return [items[(start + i) % len(items)] for i in range(n)]


_MARKET_WORDS = (("市場別：台股", "TW"), ("市場別：美股", "US"), ("市場別：總經", "MACRO"))
_TW_CODE = re.compile(r"[（(](\d{4})\s*TT[)）]")
_US_CODE = re.compile(r"[（(]([A-Z]{1,5})\s*US[)）]")


# ── 各任務 ───────────────────────────────────────────────────────────────


def _tag(prompt: str) -> str:
    body = report_body(prompt)
    market = next((code for word, code in _MARKET_WORDS if word in body), None)
    tw = list(dict.fromkeys(_TW_CODE.findall(body)))
    us = list(dict.fromkeys(_US_CODE.findall(body)))
    stocks = tw or us
    obj = {
        "market": market,
        "is_research": market is not None,
        "confidence": 0.9 if market else 0.2,
        "instrument_types": ["equity"] if stocks else ([] if market is None else ["index"]),
        "relates_stock": bool(stocks),
        "relates_futures": False,
        "stock_targets": stocks,
        "futures_targets": [],
    }
    return json.dumps(obj, ensure_ascii=False)


def _summary(prompt: str) -> str:
    picked = _pick(sentences(report_body(prompt)), 2, prompt)
    text = "".join(picked) or "本篇為示範語料。"
    return json.dumps({"summary": f"{DEMO_MARK}{text}"[:300]}, ensure_ascii=False)


def _title(prompt: str) -> str:
    body = report_body(prompt)
    head = next((ln.strip() for ln in body.splitlines() if len(ln.strip()) >= 6), "示範研報")
    return json.dumps({"title": f"{head[:40]}（示範）", "title_source": "body"}, ensure_ascii=False)


def _takeaway(prompt: str) -> str:
    picked = _pick(sentences(report_body(prompt)), 3, prompt)
    items = [{"claim": f"{DEMO_MARK}{s[:40]}", "quote": s} for s in picked]
    return json.dumps({"takeaways": items}, ensure_ascii=False)


_RATING = re.compile(r"「([^」]{2,8})」評等")
_TARGET = re.compile(r"目標價\s*(?:NT\$|US\$|\$)?\s*([\d,]+(?:\.\d+)?)")


def _signal(prompt: str) -> str:
    codes = [c for c in re.split(r"[、,\s]+", _between(prompt, "（只擷取這些代碼）：", "\n")) if c]
    body = report_body(prompt)
    # 以段落（空行分隔）內的句號切句，段內的換行原樣保留：evidence 要與內文逐字相同。
    lines = [s.strip() for para in body.split("\n\n") for s in re.findall(r"[^。]+。", para)]
    out = []
    for code in codes:
        own = [s for s in lines if code in s]
        if not own:
            continue
        item: dict = {"instrument_code": code}
        for s in own:
            m = _RATING.search(s)
            if m:
                item["rating"] = {"raw": m.group(1), "evidence": s}
                break
        for s in own:
            m = _TARGET.search(s)
            if m:
                currency = "USD" if "US$" in s else "TWD"
                item["target_price"] = {"value": float(m.group(1).replace(",", "")), "currency": currency,
                                        "horizon": "12M", "evidence": s}
                break
        thesis_s = next((s for s in lines if "展望" in s), None)
        risk_s = next((s for s in lines if "風險" in s), None)
        thesis = {}
        if thesis_s:
            thesis["outlook"] = {"stance": "positive", "summary": f"{DEMO_MARK}展望正向", "evidence": thesis_s}
        if risk_s:
            thesis["risk"] = {"stance": "stable", "summary": f"{DEMO_MARK}風險持平", "evidence": risk_s}
        if thesis:
            item["thesis"] = thesis
        out.append(item)
    return json.dumps({"signals": out}, ensure_ascii=False)


def _brief(prompt: str) -> str:
    return (
        "## 今日重點（示範）\n\n"
        "- 本份簡報由示範環境的假 LLM 產生，內容取自虛構的合成研報，不構成任何投資建議。\n"
        "- 今日窗期內的研報與評等變動已列於下方來源清單，數字皆為虛構。\n"
    )


def _condense(prompt: str) -> str:
    question = _between(prompt, "追問：").strip() or "示範問題"
    return f"QUERY: {question}\nROUTE: CORPUS_QA"


def _followup(prompt: str) -> str:
    return json.dumps(["這份報告的主要風險是什麼？", "評等與目標價有什麼變化？", "還有哪些報告談到同一檔標的？"],
                      ensure_ascii=False)


_SOURCE_HEAD = re.compile(r"^\[(\d+)\] 報告：", re.MULTILINE)


def answer_text(prompt: str) -> str:
    numbers = sorted({int(n) for n in _SOURCE_HEAD.findall(prompt)})
    if not numbers:
        return f"{DEMO_MARK}目前的示範語料裡找不到可以回答這個問題的研報。"
    first, last = numbers[0], numbers[-1]
    parts = [
        f"{DEMO_MARK}根據示範語料中的虛構研報，相關論點如下：",
        f"第一份來源指出該標的的展望維持正向[{first}]。",
    ]
    if last != first:
        parts.append(f"另一份來源同樣提到評等與目標價的變動[{last}]。")
    parts.append(f"這一句刻意引用不存在的來源[{INVALID_CITATION_NUMBER}]，系統應將它標為無效引用。")
    parts.append("以上內容皆為虛構，僅供系統示範。")
    return "".join(parts)


def _judge(system: str, prompt: str) -> str:
    if "statement" in system and "拆解" in system:
        answer = _between(prompt, "回答：") or prompt
        claims = [s for s in re.findall(r"[^。]+。", answer) if re.search(r"\d", s)][:4]
        return json.dumps({"statements": claims}, ensure_ascii=False)
    if "verdicts" in system:
        claims = re.findall(r"^(\d+)\. ", _between(prompt, "主張：\n"), re.MULTILINE)
        return json.dumps({"verdicts": [{"idx": int(i), "supported": True} for i in claims]})
    return json.dumps({"verdicts": []})


def respond(model: str, messages: list[dict]) -> str | None:
    """模型名＋訊息 → 回答全文；不認得的模型回 None（伺服器回 404 model_not_found）。"""
    task = TASK_BY_MODEL.get(model)
    if task is None:
        return None
    system = "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
    prompt = "\n".join(str(m.get("content") or "") for m in messages if m.get("role") != "system")
    if task == "tag":
        return _tag(prompt)
    if task == "summary":
        return _summary(prompt)
    if task == "title":
        return _title(prompt)
    if task == "takeaway":
        return _takeaway(prompt)
    if task == "signal":
        return _signal(prompt)
    if task == "brief":
        return _brief(prompt)
    if task == "ask_intent":
        return "CORPUS_QA"
    if task == "ask_condense":
        return _condense(prompt)
    if task == "qa_planner":
        return json.dumps({"subqueries": []})
    if task == "ask_followup":
        return _followup(prompt)
    if task == "ask_answer":
        return answer_text(prompt)
    return _judge(system, prompt)


def stream_pieces(text: str) -> list[str]:
    """串流的分段：每段約 12 字，並刻意把每個 `[n]` 從中間切開（`[` 與 `n]` 分在兩段）。"""
    pieces: list[str] = []
    buf = ""
    for ch in text:
        if ch == "[" and buf:
            pieces.append(buf + ch)
            buf = ""
            continue
        buf += ch
        if len(buf) >= 12 and ch != "[":
            pieces.append(buf)
            buf = ""
    if buf:
        pieces.append(buf)
    return pieces


def _usage(prompt_chars: int, text: str) -> dict:
    prompt_tokens = max(1, prompt_chars // 2)
    completion = max(1, len(text) // 2)
    return {"prompt_tokens": prompt_tokens, "completion_tokens": completion,
            "total_tokens": prompt_tokens + completion, "prompt_tokens_details": {"cached_tokens": 0}}


# ── HTTP ─────────────────────────────────────────────────────────────────


class Handler(BaseHTTPRequestHandler):
    server_version = "fake-llm/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 — 簽章同父類別；只記一行摘要，不記 prompt
        sys.stderr.write("[fake-llm] " + (format % args) + "\n")

    def _json(self, status: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        expected = os.getenv("FAKE_LLM_API_KEY", "")
        got = self.headers.get("Authorization", "")
        if not got.startswith("Bearer ") or not got[7:].strip():
            return False
        return not expected or got[7:] == expected

    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            return self._json(200, {"status": "ok"})
        if self.path.rstrip("/") == "/v1/models":
            if not self._authorized():
                return self._json(401, {"error": {"message": "missing bearer token", "code": "invalid_api_key"}})
            return self._json(200, {"object": "list", "data": [
                {"id": m, "object": "model", "owned_by": "demo"} for m in sorted(set(MODELS.values()))]})
        return self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):  # noqa: N802
        if self.path.rstrip("/") != "/v1/chat/completions":
            return self._json(404, {"error": {"message": "not found"}})
        if not self._authorized():
            return self._json(401, {"error": {"message": "missing bearer token", "code": "invalid_api_key"}})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": {"message": "invalid JSON body"}})
        model = str(body.get("model") or "")
        messages = body.get("messages") if isinstance(body.get("messages"), list) else []
        # openai_compatible 送 max_tokens（不是 max_completion_tokens）：缺了就是 adapter 的對應規則壞了。
        if not isinstance(body.get("max_tokens"), int):
            return self._json(400, {"error": {"message": "max_tokens is required", "code": "invalid_request"}})
        text = respond(model, messages)
        if text is None:
            return self._json(404, {"error": {"message": f"model {model!r} not found", "code": "model_not_found"}})
        prompt_chars = sum(len(str(m.get("content") or "")) for m in messages)
        if not body.get("stream"):
            return self._json(200, {
                "id": "demo-" + hashlib.sha256(text.encode()).hexdigest()[:12], "object": "chat.completion",
                "model": model, "system_fingerprint": "fake-llm",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": _usage(prompt_chars, text),
            })
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def send(obj) -> None:
            payload = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()

        base = {"id": "demo-stream", "object": "chat.completion.chunk", "model": model}
        send({**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]})
        for piece in stream_pieces(text):
            send({**base, "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]})
        send({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        if (body.get("stream_options") or {}).get("include_usage"):
            send({**base, "choices": [], "usage": _usage(prompt_chars, text)})
        send("[DONE]")


def main() -> None:
    port = int(os.getenv("FAKE_LLM_PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    sys.stderr.write(f"[fake-llm] listening on :{port}; models: {declared_models()}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
