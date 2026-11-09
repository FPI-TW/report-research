"""就緒判定（`GET /readyz`，web/routers/health.py）：這個行程能不能開始接流量。

`/healthz` 只回答「DB 活著嗎」（外部監控與負載平衡器的存活探測，形狀釘死、不動）。容器部署另外需要
「可以把流量導進來了嗎」：DB 可用之外，問答與檢索要的嵌入模型、profile、LLM 設定也都要就位。
四項全過才就緒；任何一項不過，端點回 503，**哪一項不過只進日誌**（對外免認證端點不洩漏細節，
與 `/healthz` 同一個保守原則）。

- `db`：與 `/healthz` 共用同一份 DB 探測（呼叫端傳進來）。
- `embedding`：BGE-M3 已載入，或權重在本機 HF 快取裡可以離線載入（只看檔案、不載模型；
  背景暖機要數十秒，就緒不必等它跑完——第一個查詢會等暖機，但不會失敗）。
- `profile`：`app.profile.get_profile()` 驗證通過（profile 啟動時嚴格驗證、錯就拒起）。
  找不到 `app.profile` 這個模組本身（沒有 profile 機制的舊版程式）視為通過——沒有 profile 可驗。
- `llm`：送達路徑（`LLM_PROVIDER`）是合法值，而且 `llm_models.diagnose` 沒有 ERROR（金鑰有值、模型名在
  目前 provider 的白名單、端點設定沒有錯、網搜總閘沒開）。**不呼叫 LLM**：額度與連線是 `/healthz/llm` 的事，
  就緒不該因為供應商一時不通而把整個站台從負載平衡器摘掉（檢索、閱讀頁、雷達、簡報都不需要 LLM）。

每一項都不拋例外：判斷本身失敗就是「不就緒」。
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
from pathlib import Path
from typing import Awaitable, Callable, Mapping

logger = logging.getLogger(__name__)

CHECKS = ("db", "embedding", "profile", "llm")
# 嵌入權重的判斷要讀檔案系統（HF 快取），丟執行緒並設上限，不讓探測卡住事件迴圈。
_FS_CHECK_TIMEOUT = 3.0
_WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")


def embedding_ready() -> bool:
    """BGE-M3 已在記憶體，或本機快取裡有完整的 snapshot（config 與權重檔）。"""
    from app.services import embed

    if embed._model is not None:
        return True
    try:
        from huggingface_hub import snapshot_download

        path = Path(snapshot_download(embed.MODEL_NAME, local_files_only=True))
    except Exception:
        logger.info("readyz：本機快取找不到嵌入模型 %s", embed.MODEL_NAME, exc_info=True)
        return False
    return (path / "config.json").is_file() and any((path / name).is_file() for name in _WEIGHT_FILES)


def profile_ready() -> bool:
    try:
        # 以完整模組名載入：`from app import profile` 在模組不存在時拋的是 ImportError（不是
        # ModuleNotFoundError），分不出「沒有這個模組」與「模組自己 import 壞了」。
        profile = importlib.import_module("app.profile")
    except ModuleNotFoundError as exc:
        if exc.name == "app.profile":
            return True  # 這個版本還沒有 profile 機制
        logger.warning("readyz：載入 app.profile 失敗", exc_info=True)
        return False
    try:
        profile.get_profile()
    except Exception:
        logger.warning("readyz：profile 驗證失敗", exc_info=True)
        return False
    return True


def llm_config_ready(env: Mapping[str, str] | None = None) -> bool:
    from app.config import get_settings
    from app.services import llm_models

    env = os.environ if env is None else env
    raw = (env.get("LLM_PROVIDER") or "").strip().lower()
    if raw and raw not in llm_models.PROVIDERS:
        return False  # provider() 會退回預設並記 ERROR；就緒判定不跟著放過拼錯的值
    prov = llm_models.provider(env)
    resolved = llm_models.resolve_all(llm_models.ONLINE_TASKS, env=env)
    # 與 web/server.py 的啟動自檢同一組判準（白名單與端點設定都依目前 provider）。
    findings = llm_models.diagnose(
        resolved,
        has_key=bool((env.get(llm_models.key_env(env)) or "").strip()),
        web_enabled=get_settings().ask_enable_web,
        prov=prov,
        allowed=llm_models.allowed_models(env),
        problems=llm_models.config_problems(env),
    )
    return not any(level >= logging.ERROR for level, _ in findings)


async def _sync_check(fn: Callable[[], bool], name: str) -> bool:
    try:
        return bool(await asyncio.wait_for(asyncio.to_thread(fn), timeout=_FS_CHECK_TIMEOUT))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("readyz：%s 判斷失敗或逾時", name, exc_info=True)
        return False


async def failed_checks(db_ok: Callable[[], Awaitable[bool]]) -> list[str]:
    """回沒通過的項目名（`CHECKS` 的子集，依序）；空清單＝就緒。永不拋例外。"""
    failed: list[str] = []
    try:
        if not await db_ok():
            failed.append("db")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("readyz：DB 探測失敗", exc_info=True)
        failed.append("db")
    if not await _sync_check(embedding_ready, "embedding"):
        failed.append("embedding")
    if not await _sync_check(profile_ready, "profile"):
        failed.append("profile")
    try:
        if not llm_config_ready():
            failed.append("llm")
    except Exception:
        logger.warning("readyz：LLM 設定判斷失敗", exc_info=True)
        failed.append("llm")
    return failed
