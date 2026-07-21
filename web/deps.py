# web/deps.py
"""web 層的共用 helper、設定，與可 mock 的服務依賴面。拆分 web/server.py 的地基。

## 存取方式：一律走模組物件，不要 `from web.deps import X`

路由模組請寫：

    from web import deps
    ...
    if not deps._valid_uuid(x): ...

而不是 `from web.deps import _valid_uuid`。

理由是測試以 `deps.X = fake` 覆寫來注入假物件。`from web.deps import X` 會在
import 當下把值複製進該模組自己的命名空間，之後改 `web.deps.X` 對它無效——
patch 會**安靜落空**（不是報錯，是測試照樣綠但根本沒 patch 到）。這正是原本
所有綁定都擠在 web/server.py 時、拆分會踩到的坑。

## 為什麼這裡自己載 .env

web/auth.py 在 import 時就 fail-closed 檢查 REPORT_MARK_ACCESS_USERNAME/_PASSWORD
（見 web/auth.py 開頭），所以 .env 必須早於它被載入。原本這個順序只由
web/server.py 開頭的 load_env_file 保證；一旦路由模組可以被獨立 import（拆分後
必然如此），先碰到 auth 的那條路徑就會在 import 期 RuntimeError。

load_env_file 預設 override=False（只填未存在的鍵），重複呼叫無副作用，故這裡
直接自備一份，讓任何 import 順序都安全，而不是依賴呼叫端的紀律。

驗證這件事時注意：**git worktree 內沒有 .env**（它被 gitignore，不隨 worktree
複製），load_env_file 遇不到檔案會直接 return，於是「先 import deps」與「不 import
deps」都會 fail-closed 失敗——看起來像機制無效，其實只是沒檔案可載。要在 worktree
裡驗，得先把 .env 複製進來。
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid as _uuidlib
from contextlib import suppress as _suppress
from pathlib import Path

from web.env_loader import load_env_file

# 見 docstring：不可假設 web/server.py 已先載過。
load_env_file(Path(__file__).resolve().parents[1] / ".env")


STATIC_DIR = Path(__file__).resolve().parent / "static"


def _sse(event: str, data: object) -> str:
    """組一個 SSE 事件框（event + json data）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


SSE_HEARTBEAT_INTERVAL = float(os.getenv("SSE_HEARTBEAT_INTERVAL", "20"))


async def _with_heartbeat(gen, interval: float = SSE_HEARTBEAT_INTERVAL):
    """事件之間插入 SSE 註解心跳，避免長靜默被反向代理切斷連線。

    nginx 的 proxy_read_timeout 是 60s（deploy/nginx.conf），但兩條生成路徑都有
    超過它的靜默窗：研報逐節路徑每節的針對性檢索（含 rerank）可達 90s+ 完全無事件，
    單次路徑的 run-level 檢索亦然。缺了心跳，連線會在生成中途被 nginx 切斷。

    這條路徑本機看不到：eval 直接呼叫 generate_report、單元測試不走 HTTP、開發時
    直連 :8097 也繞過 nginx——只有經 Cloudflare Tunnel + nginx 的實際使用者會遇到，
    且表現為「生成到一半連線就斷」，容易被誤認為偶發網路問題。

    心跳是 SSE 註解行（`: ...`）：前端 readSSE.parseFrame 對無 `data:` 欄位的框回
    null 而略過，故對既有事件契約零影響。
    """
    it = gen.__aiter__()
    pending: asyncio.Task | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(it.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield ": keep-alive\n\n"
                continue
            task, pending = pending, None
            try:
                yield task.result()
            except StopAsyncIteration:
                return
    finally:
        # 用戶端中斷時，先收掉尚未完成的 __anext__，再讓底層產生器跑自己的 finally
        # （研報逐節路徑靠它 kill 子程序）。
        if pending is not None:
            pending.cancel()
            with _suppress(BaseException):
                await pending
        aclose = getattr(it, "aclose", None)
        if aclose is not None:
            await aclose()


def _valid_uuid(s) -> bool:
    """路徑/請求體帶進來的 id 是否為合法 UUID。

    ask / report / qa_versions 三組都用它擋非法 id。拆分時若把它跟著其中一組
    搬走，其餘兩組會 NameError——qa_versions 那條的覆蓋由
    tests/test_pre_split_guards.py 補上。
    """
    try:
        _uuidlib.UUID(str(s))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


# ── 服務層依賴面：web 層唯一可被 mock 的外部呼叫集合 ──────────────────────
#
# 路由 handler 一律以 deps.X(...) 呼叫這些；測試一律 patch web.deps.X。
# 集中在此的目的：拆分成 router 模組後，這些函式被跨模組使用（handler 在 router
# 模組、實作在 app.services），patch 目標必須是雙方都看得到的單一位置＝web.deps。
# 若讓各 router 各自 `from app.services import X`，就回到 from-import 陷阱：
# `web.deps.X = fake` 改不到 router 內部那份綁定，patch 安靜落空。
#
# 只放「測試會 mock 的服務函式」；純資料轉換（source_display / clean_text /
# MARKETS 等，測試不 mock）仍由各處直接 import，不進這裡。
from app.services.answer import (  # noqa: E402
    answer_question,
    delete_qa,
    list_qa_versions,
    log_stopped_qa,
)
from app.services.db import SessionFactory  # noqa: E402
from app.services.embed import embed_query_cached, embed_texts  # noqa: E402
from app.services.radar import (  # noqa: E402
    fetch_broker_coverage_counts,
    fetch_broker_signals,
    fetch_coverage_counts,
    fetch_instrument_signals,
    fetch_signals_for_instruments,
    list_radar_instruments,
)
from app.services.reading.queries import (  # noqa: E402
    fetch_chunk_content,
    fetch_doc,
    fetch_signals,
    fetch_similar,
    fetch_takeaways,
)
from app.services.rerank import warmup as rerank_warmup  # noqa: E402
from app.services.retrieval import hybrid_search, rank_reports  # noqa: E402
