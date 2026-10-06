"""asyncio Unix socket server 與 CLI。

連線流程：accept → `SO_PEERCRED` 取對端 uid（不在 catalog 的 `allowed_uids` 就回 `forbidden_peer` 並斷線）
→ 逐行讀請求（每行上限 `MAX_REQUEST_BYTES`、閒置逾時）→ 每個請求有 `request_timeout`、同時處理數
上限 `max_concurrent` → 一行回應（上限 `max_response_bytes`）。

socket 權限是第一道（目錄 0750、socket 0660，群組＝代理的主群組，web 的使用者加入該群組才連得到），
uid 白名單是第二道：群組多加了誰，也還要 catalog 點名。

CLI：
    python3 -m ops_agent --catalog /opt/report-mark-ops/services.prod.toml            # 常駐（systemd 用）
    python3 -m ops_agent --catalog deploy/ops/services.dev.toml --socket /tmp/x.sock  # dev 冒煙，臨時 socket
    python3 -m ops_agent --catalog deploy/ops/services.prod.toml --check              # 只驗 catalog

退出碼：0 正常收場（含 SIGTERM）；2 catalog／綁定設定錯誤（拒絕啟動）；1 其他啟動失敗。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import socket
import stat
import struct
import sys
import time
from datetime import datetime, timezone

from ops_agent import backends
from ops_agent.catalog import Catalog, CatalogError, load_catalog, validate_binding
from ops_agent.protocol import (
    LOG_PARAM_KEYS,
    MAX_REQUEST_BYTES,
    OPS,
    ProtocolError,
    encode,
    error_response,
    ok_response,
    parse_lines,
    parse_request,
    parse_since,
)
from ops_agent.runner import SubprocessRunner

logger = logging.getLogger("ops_agent")

IDLE_TIMEOUT = 10.0           # 連上後多久沒送完一行就斷線（擋住佔著連線不送的客戶端）
_ENVELOPE_RESERVE = 4096      # 回應裡 entries 以外的欄位預留


def peer_uid(sock) -> int | None:
    """SO_PEERCRED：(pid, uid, gid)。取不到（不是 Unix socket、平台不支援）回 None＝拒絕。"""
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    except (OSError, AttributeError):
        return None
    _pid, uid, _gid = struct.unpack("3i", raw)
    return uid


class Agent:
    def __init__(self, catalog: Catalog, runner=None, *, peer_uid_fn=peer_uid):
        self.catalog = catalog
        self.runner = runner or SubprocessRunner()
        self.peer_uid_fn = peer_uid_fn
        self._inflight = 0

    # ── 請求處理（不碰 socket，測試直接呼叫）──────────────────────────────
    async def handle_line(self, line: bytes) -> dict:
        env = self.catalog.environment
        started = time.monotonic()
        req_id, op, service, code = None, None, None, "ok"
        try:
            req = parse_request(line)
            req_id, op, service = req["id"], req["op"], req["service"]
            if req["env"] != env:
                raise ProtocolError("environment_mismatch",
                                    f"這個代理只服務 {env}，請求宣告的是 {req['env']}")
            if self._inflight >= self.catalog.agent.max_concurrent:
                raise ProtocolError("busy", "同時處理中的請求已達上限，請稍後再試")
            self._inflight += 1
            try:
                async with asyncio.timeout(self.catalog.agent.request_timeout):
                    result = await self._dispatch(req)
            except TimeoutError as exc:
                raise ProtocolError("timeout", f"請求超過 {self.catalog.agent.request_timeout:g} 秒") from exc
            finally:
                self._inflight -= 1
            resp = ok_response(req_id, env, result)
            if len(encode(resp)) > self.catalog.agent.max_response_bytes:
                raise ProtocolError("response_too_large", "回應超過上限")
            return resp
        except ProtocolError as exc:
            code = exc.code
            return error_response(req_id, env, exc.code, exc.message)
        except Exception:  # noqa: BLE001 - 代理不因單一請求的程式錯誤而停
            code = "internal_error"
            logger.exception("請求處理失敗 op=%s service=%s", op, service)
            return error_response(req_id, env, "internal_error", "代理內部錯誤（細節在代理的 journal）")
        finally:
            logger.info("request op=%s service=%s code=%s ms=%d", op, service, code,
                        (time.monotonic() - started) * 1000)

    def _service(self, name: str, action: str):
        svc = self.catalog.get(name)
        if svc is None:
            raise ProtocolError("unknown_service", f"catalog 沒有服務 {name!r}")
        if action not in svc.actions:
            raise ProtocolError("action_not_allowed", f"服務 {name} 不允許 {action}")
        return svc

    async def _dispatch(self, req: dict) -> dict:
        op = req["op"]
        now = datetime.now(timezone.utc)
        checked_at = backends.iso_utc(int(now.timestamp()))
        if op == "list":
            queryable = [s for s in self.catalog.services if "status" in s.actions]
            rows = {r["name"]: r for r in await backends.query_status(self.catalog, self.runner, queryable)}
            items = []
            for svc in self.catalog.services:
                row = rows.get(svc.name)
                if row is None:
                    row = {**svc.public(), "summary": "unknown", "error": "此服務不允許 status",
                           "systemd": None, "container": None, "timer_state": None}
                items.append(row)
            return {"environment": self.catalog.environment, "host": socket.gethostname(),
                    "checked_at": checked_at, "items": items}
        if op == "status":
            svc = self._service(req["service"], OPS[op])
            row = (await backends.query_status(self.catalog, self.runner, [svc]))[0]
            return {**row, "checked_at": checked_at}
        if op == "logs":
            svc = self._service(req["service"], OPS[op])
            params = req["params"]
            extra = set(params) - LOG_PARAM_KEYS
            if extra:
                raise ProtocolError("invalid_params", f"logs 只收 since、lines，不收 {sorted(extra)}")
            cfg = self.catalog.agent
            lines = parse_lines(params.get("lines"), cfg.max_log_lines)
            since = parse_since(params.get("since"), cfg.max_log_age, now=now)
            result = await backends.query_logs(self.catalog, self.runner, svc, since=since, lines=lines,
                                               budget=cfg.max_response_bytes - _ENVELOPE_RESERVE)
            return {**result, "checked_at": checked_at}
        raise ProtocolError("unknown_op", f"不支援的操作：{op!r}")  # parse_request 已擋，保險

    # ── 連線 ────────────────────────────────────────────────────────────
    async def handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        env = self.catalog.environment
        try:
            uid = self.peer_uid_fn(writer.get_extra_info("socket"))
            if uid is None or uid not in self.catalog.agent.allowed_uids:
                logger.warning("拒絕連線：對端 uid=%s 不在允許清單", uid)
                writer.write(encode(error_response(None, env, "forbidden_peer", "這個使用者不能連線到維運代理")))
                await writer.drain()
                return
            while True:
                try:
                    line = await asyncio.wait_for(reader.readline(), IDLE_TIMEOUT)
                except TimeoutError:
                    return
                except (ValueError, asyncio.LimitOverrunError):
                    writer.write(encode(error_response(None, env, "request_too_large",
                                                       f"一行請求最多 {MAX_REQUEST_BYTES} bytes")))
                    await writer.drain()
                    return
                if not line:
                    return
                writer.write(encode(await self.handle_line(line)))
                await writer.drain()
        except (ConnectionError, BrokenPipeError):
            return
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, BrokenPipeError):
                pass


def _prepare_socket_path(path: str) -> None:
    """舊的 socket 檔（上次沒收乾淨）先移除；同路徑是別的東西就拒絕，不覆蓋。"""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(st.st_mode):
        raise CatalogError(f"{path} 已存在且不是 socket，拒絕覆蓋")
    os.unlink(path)


async def serve(agent: Agent, socket_path: str, stop: asyncio.Event) -> None:
    _prepare_socket_path(socket_path)
    old_umask = os.umask(0o117)  # socket 以 0660 建立：bind 與 chmod 之間沒有更寬的空窗
    try:
        server = await asyncio.start_unix_server(agent.handle_connection, path=socket_path,
                                                 limit=MAX_REQUEST_BYTES)
    finally:
        os.umask(old_umask)
    os.chmod(socket_path, 0o660)
    logger.info("ops agent 啟動 environment=%s socket=%s services=%d", agent.catalog.environment, socket_path,
                len(agent.catalog.services))
    try:
        async with server:
            await stop.wait()
    finally:
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass
        logger.info("ops agent 停止")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ops_agent", description="report-mark 維運代理（唯讀：status、logs）")
    ap.add_argument("--catalog", required=True, help="Service Catalog（TOML）")
    ap.add_argument("--socket", help="覆寫 socket 路徑（只限 development；production 一律用固定路徑）")
    ap.add_argument("--check", action="store_true", help="只驗證 catalog 與綁定，不啟動")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    try:
        catalog = load_catalog(args.catalog)
        socket_path = args.socket or catalog.socket_path
        validate_binding(catalog, socket_path)
    except CatalogError as exc:
        print(f"拒絕啟動：{exc}", file=sys.stderr)
        return 2
    if args.check:
        print(f"catalog OK：environment={catalog.environment} socket={socket_path} "
              f"services={len(catalog.services)} allowed_uids={sorted(catalog.agent.allowed_uids)}")
        return 0

    async def _run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await serve(Agent(catalog), socket_path, stop)

    try:
        asyncio.run(_run())
    except CatalogError as exc:
        print(f"拒絕啟動：{exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"啟動失敗：{exc}", file=sys.stderr)
        return 1
    return 0
