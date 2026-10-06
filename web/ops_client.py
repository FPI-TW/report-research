"""維運代理的 client（async）：連 Unix socket、送一行 JSON、讀一行回應。協定見 `ops_agent/protocol.py`。

web 不直接呼叫 systemctl／journalctl／docker；維運端點（`web/routers/admin_ops.py`）只經這裡問代理。
**fail-open**：代理沒裝、沒啟動、socket 權限不對、逾時、環境不符，一律拋 `OpsAgentUnavailable`，
路由回 503 `ops_agent_unavailable`——web 其他功能不受影響，啟動也不檢查代理在不在。

兩種錯誤：
- `OpsAgentUnavailable`：連不上或代理不肯服務這個 web（`forbidden_peer`、`environment_mismatch`、
  回應不是合法的協定）。訊息給管理員看，指向該查哪裡。
- `OpsAgentError`：代理正常回應但拒絕這個請求（`unknown_service`、`invalid_params`、`command_timeout` 等），
  帶代理的穩定 code。

測試替換點：`default_client`（路由每次呼叫它取 client），換成指向假代理（`tests/fake_ops_agent.py`）的
`OpsClient`。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import suppress

from app.config import get_settings
from ops_agent.protocol import PROTOCOL_VERSION, encode

# 代理那端的回應上限可設到 4 MiB（catalog 的 max_response_bytes 上限）；這裡多留一點給換行與誤差。
MAX_RESPONSE_BYTES = 4 * 1024 * 1024 + 4096

# 代理拒絕服務這個 web 的 code：對呼叫端而言等同「代理不可用」（是部署設定問題，不是請求本身的問題）。
_UNAVAILABLE_CODES = frozenset({"forbidden_peer", "environment_mismatch"})


class OpsAgentUnavailable(Exception):
    pass


class OpsAgentError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class OpsClient:
    def __init__(self, socket_path: str, environment: str, timeout: float = 20.0):
        self.socket_path = socket_path
        self.environment = environment
        self.timeout = timeout

    async def request(self, op: str, service: str | None = None, params: dict | None = None) -> dict:
        if not self.environment or not self.socket_path:
            raise OpsAgentUnavailable("維運代理未設定（OPS_AGENT_ENVIRONMENT 不合法）")
        req_id = uuid.uuid4().hex[:16]
        req: dict = {"v": PROTOCOL_VERSION, "id": req_id, "env": self.environment, "op": op}
        if service is not None:
            req["service"] = service
        if params:
            req["params"] = params
        try:
            async with asyncio.timeout(self.timeout):
                reader, writer = await asyncio.open_unix_connection(self.socket_path, limit=MAX_RESPONSE_BYTES)
                try:
                    writer.write(encode(req))
                    await writer.drain()
                    line = await reader.readline()
                finally:
                    writer.close()
                    with suppress(Exception):
                        await writer.wait_closed()
        except TimeoutError as exc:
            raise OpsAgentUnavailable(f"維運代理逾時（{self.timeout:g} 秒）") from exc
        except FileNotFoundError as exc:
            raise OpsAgentUnavailable(f"維運代理未啟動（找不到 {self.socket_path}）") from exc
        except PermissionError as exc:
            raise OpsAgentUnavailable("沒有權限連線到維運代理（web 的使用者不在 socket 的群組）") from exc
        except (OSError, ValueError, asyncio.LimitOverrunError, asyncio.IncompleteReadError) as exc:
            raise OpsAgentUnavailable(f"維運代理連線失敗：{type(exc).__name__}") from exc
        if not line:
            raise OpsAgentUnavailable("維運代理關閉了連線")
        try:
            resp = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise OpsAgentUnavailable("維運代理的回應不是合法 JSON") from exc
        if not isinstance(resp, dict) or resp.get("v") != PROTOCOL_VERSION:
            raise OpsAgentUnavailable("維運代理的協定版本不符")
        error = resp.get("error") if isinstance(resp.get("error"), dict) else {}
        code = error.get("code") if isinstance(error.get("code"), str) else "internal_error"
        message = error.get("message") if isinstance(error.get("message"), str) else "維運代理拒絕請求"
        if code in _UNAVAILABLE_CODES and resp.get("ok") is not True:
            raise OpsAgentUnavailable(message)
        if resp.get("env") != self.environment:
            raise OpsAgentUnavailable(f"維運代理服務的環境是 {resp.get('env')!r}，不是 {self.environment!r}")
        if resp.get("id") not in (req_id, None):
            raise OpsAgentUnavailable("維運代理的回應對不上請求")
        if resp.get("ok") is True and isinstance(resp.get("result"), dict):
            return resp["result"]
        raise OpsAgentError(code, message)


def default_client() -> OpsClient:
    s = get_settings()
    return OpsClient(s.ops_agent_socket, s.ops_agent_environment, s.ops_agent_timeout)
