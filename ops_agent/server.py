"""asyncio Unix socket server 與 CLI。

連線流程：accept → `SO_PEERCRED` 取對端 uid（不在 catalog 的 `allowed_uids` 就回 `forbidden_peer` 並斷線）
→ 逐行讀請求（每行上限 `MAX_REQUEST_BYTES`、閒置逾時）→ 每個請求有 `request_timeout`、同時處理數
上限 `max_concurrent` → 一行回應（上限 `max_response_bytes`）→ 若是 `restart`，回應送出（drain）之後才在
背景執行（`Reply.after`；重啟 Web 會中斷這次請求本身，先送回應前端才拿得到 202）。

socket 權限是第一道（目錄 0750、socket 0660，群組＝代理的主群組，web 的使用者加入該群組才連得到），
uid 白名單是第二道：群組多加了誰，也還要 catalog 點名。

CLI：
    python3 -m ops_agent --catalog /opt/report-mark-ops/services.prod.toml            # 常駐（systemd 用）
    python3 -m ops_agent --catalog deploy/ops/services.dev.toml --socket /tmp/x.sock  # dev 冒煙，臨時 socket
    python3 -m ops_agent --catalog deploy/ops/services.prod.toml --check              # 只驗 catalog（含依賴圖）

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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from ops_agent import actions, backends
from ops_agent.catalog import Catalog, CatalogError, load_catalog, validate_binding
from ops_agent.protocol import (
    LOG_PARAM_KEYS,
    MAX_REQUEST_BYTES,
    OPS,
    WRITE_ACTIONS,
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
_SHUTDOWN_GRACE = 10.0        # 收到 SIGTERM 時等背景的 restart 跑完多久


@dataclass
class Reply:
    response: dict
    after: Callable[[], Awaitable[None]] | None = None  # 回應送出之後才執行（restart）


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
        # execution group → 忙碌中（從接受寫入類請求到 systemctl 回來）。同一個 event loop 內的檢查與標記
        # 之間沒有 await，兩個幾乎同時到的請求不會都通過。
        self._busy_groups: set[str] = set()
        self._background: set[asyncio.Task] = set()

    # ── 請求處理（不碰 socket，測試直接呼叫）──────────────────────────────
    async def handle_line(self, line: bytes) -> dict:
        """處理一行並回傳回應；有延後動作（restart）就立刻排進背景。socket 路徑用 `handle`，先送回應。"""
        reply = await self.handle(line)
        if reply.after is not None:
            self.spawn(reply.after)
        return reply.response

    def spawn(self, after: Callable[[], Awaitable[None]]) -> None:
        task = asyncio.get_running_loop().create_task(after())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def drain_background(self, timeout: float = _SHUTDOWN_GRACE) -> None:
        if self._background:
            await asyncio.wait(set(self._background), timeout=timeout)

    async def handle(self, line: bytes) -> Reply:
        env = self.catalog.environment
        started = time.monotonic()
        req_id, op, service, code, actor = None, None, None, "ok", None
        pending: list = []  # 這個請求的延後動作（restart）；每個請求一份，不放在 self 上
        try:
            req = parse_request(line)
            req_id, op, service, actor = req["id"], req["op"], req["service"], req["actor"]
            if req["env"] != env:
                raise ProtocolError("environment_mismatch",
                                    f"這個代理只服務 {env}，請求宣告的是 {req['env']}")
            if self._inflight >= self.catalog.agent.max_concurrent:
                raise ProtocolError("busy", "同時處理中的請求已達上限，請稍後再試")
            self._inflight += 1
            try:
                async with asyncio.timeout(self.catalog.agent.request_timeout):
                    result = await self._dispatch(req, pending)
            except TimeoutError as exc:
                raise ProtocolError("timeout", f"請求超過 {self.catalog.agent.request_timeout:g} 秒") from exc
            finally:
                self._inflight -= 1
            resp = ok_response(req_id, env, result)
            if len(encode(resp)) > self.catalog.agent.max_response_bytes:
                raise ProtocolError("response_too_large", "回應超過上限")
            after = pending.pop() if pending else None
            return Reply(resp, after)
        except ProtocolError as exc:
            code = exc.code
            return Reply(error_response(req_id, env, exc.code, exc.message))
        except Exception:  # noqa: BLE001 - 代理不因單一請求的程式錯誤而停
            code = "internal_error"
            logger.exception("請求處理失敗 op=%s service=%s", op, service)
            return Reply(error_response(req_id, env, "internal_error", "代理內部錯誤（細節在代理的 journal）"))
        finally:
            for after in pending:  # 沒走到回應（逾時、回應過大）：延後動作不執行，group 要放掉
                self._busy_groups.discard(after.group)
            logger.info("request op=%s service=%s actor=%s code=%s ms=%d", op, service, actor, code,
                        (time.monotonic() - started) * 1000)

    def _service(self, name: str, action: str):
        svc = self.catalog.get(name)
        if svc is None:
            raise ProtocolError("unknown_service", f"catalog 沒有服務 {name!r}")
        if action not in svc.actions:
            raise ProtocolError("action_not_allowed", f"服務 {name} 不允許 {action}")
        return svc

    async def _dispatch(self, req: dict, pending: list) -> dict:
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
            # externals：依賴圖的外部節點（代理不查它們，只照 catalog 轉交；判讀在 web）。
            return {"environment": self.catalog.environment, "host": socket.gethostname(),
                    "checked_at": checked_at, "items": items,
                    "externals": [ext.public() for ext in self.catalog.externals]}
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
        if op in WRITE_ACTIONS:
            svc = self._service(req["service"], OPS[op])
            return await self._write(svc, op, req.get("actor"), checked_at, pending)
        raise ProtocolError("unknown_op", f"不支援的操作：{op!r}")  # parse_request 已擋，保險

    # ── 寫入類（restart／run）：規則見 ops_agent/actions.py ─────────────────
    async def _write(self, svc, action: str, actor: str | None, checked_at: str, pending: list) -> dict:
        actions.check_write_allowed(svc, action)  # 硬性限制：catalog 被誤設也放不開
        group = svc.group
        if group in self._busy_groups:
            raise ProtocolError("already_running", f"execution group {group} 已有操作在進行，不排隊，請稍後再試")
        self._busy_groups.add(group)
        handed_off = False
        try:
            previous = await actions.check_units(self.catalog, self.runner, svc, action)
            actions.check_locks(self.catalog, svc)
            argv = actions.command_argv(self.catalog, svc, action)
            base = {**svc.public(), "action": action, **previous, "accepted_at": checked_at,
                    "checked_at": checked_at}
            if action == "run":
                res = await self.runner.run(argv, timeout=self.catalog.agent.command_timeout,
                                            max_bytes=actions.CMD_MAX_BYTES)
                if res.timed_out:
                    raise ProtocolError("command_timeout", "systemctl start 逾時（job 可能已排入，請看 status）")
                if res.returncode != 0:
                    detail = backends._first_line(res.stderr or res.stdout)
                    suffix = f"：{detail}" if detail else ""
                    raise ProtocolError("command_failed", f"systemctl start 失敗（rc={res.returncode}）{suffix}")
                logger.warning("run service=%s unit=%s actor=%s：已排入", svc.name, svc.unit, actor)
                return {**base, "state": "queued", "execute_after_ms": 0}
            delay = self.catalog.agent.restart_delay
            pending.append(self._deferred_restart(svc, argv, actor, delay))
            handed_off = True
            logger.warning("restart service=%s unit=%s actor=%s：已接受，%.1f 秒後執行", svc.name, svc.unit, actor,
                           delay)
            return {**base, "state": "scheduled", "execute_after_ms": int(delay * 1000)}
        finally:
            if not handed_off:
                self._busy_groups.discard(group)

    def _deferred_restart(self, svc, argv: list[str], actor: str | None, delay: float):
        async def run_restart() -> None:
            try:
                await asyncio.sleep(delay)
                res = await self.runner.run(argv, timeout=self.catalog.agent.command_timeout,
                                            max_bytes=actions.CMD_MAX_BYTES)
                if res.timed_out or res.returncode != 0:
                    logger.error("restart service=%s unit=%s actor=%s 失敗（rc=%s）：%s", svc.name, svc.unit, actor,
                                 res.returncode, backends._first_line(res.stderr or res.stdout))
                else:
                    logger.warning("restart service=%s unit=%s actor=%s：已排入", svc.name, svc.unit, actor)
            except Exception:  # noqa: BLE001 - 背景工作的錯誤只能進 journal
                logger.exception("restart service=%s 執行失敗", svc.name)
            finally:
                self._busy_groups.discard(svc.group)

        run_restart.group = svc.group  # type: ignore[attr-defined]
        return run_restart

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
                reply = await self.handle(line)
                try:
                    writer.write(encode(reply.response))
                    await writer.drain()
                finally:
                    # 回應送出之後才執行；對端斷線也照樣執行（請求已被接受，前端會用 status 輪詢）。
                    if reply.after is not None:
                        self.spawn(reply.after)
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
        await agent.drain_background()
    finally:
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass
        logger.info("ops agent 停止")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ops_agent",
                                 description="report-mark 維運代理（status、logs；白名單 restart、run）")
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
        edges = sum(len(n.depends_on) for n in (*catalog.services, *catalog.externals))
        print(f"catalog OK：environment={catalog.environment} socket={socket_path} "
              f"services={len(catalog.services)} externals={len(catalog.externals)} dependencies={edges} "
              f"allowed_uids={sorted(catalog.agent.allowed_uids)}")
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
