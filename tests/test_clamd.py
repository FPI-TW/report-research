"""app/services/clamd.py：對 thread 內的假 clamd（TCP）驗協定與 fail-closed 分類。

不連網、不需要真的 clamd。假伺服器照 clamd 的行為：指令以 `z` 開頭、`\\0` 結尾；INSTREAM 讀
4 bytes 網路序長度前綴的分塊直到 0 長度塊；超過 StreamMaxLength 時先寫一行 ERROR 再關連線。
"""

from __future__ import annotations

import io
import os
import socket
import socketserver
import struct
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import clamd  # noqa: E402

NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)


def ctime(dt: datetime) -> str:
    """clamd 的 VERSION 用 C 的 ctime 格式（個位數日期前補空白）。"""
    return dt.strftime("%a %b ") + f"{dt.day:2d}" + dt.strftime(" %H:%M:%S %Y")


def version_line(age: timedelta, engine: str = "1.4.3", db: int = 27412) -> str:
    return f"ClamAV {engine}/{db}/{ctime(NOW - age)}"


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        srv: FakeClamd = self.server.owner  # type: ignore[attr-defined]
        sock = self.request
        cmd = bytearray()
        while not cmd.endswith(b"\0"):
            b = sock.recv(1)
            if not b:
                return
            cmd += b
        name = cmd[:-1].decode()
        srv.commands.append(name)
        if srv.hang.get(name):
            srv.release.wait(5)
            return
        if name == "zPING":
            sock.sendall(b"PONG\0")
        elif name == "zVERSION":
            if srv.version_reply is not None:
                sock.sendall(srv.version_reply.encode() + b"\0")
        elif name == "zINSTREAM":
            chunks: list[int] = []
            data = bytearray()
            while True:
                head = _recv_exact(sock, 4)
                if head is None:
                    srv.streams.append((bytes(data), chunks, False))
                    return
                (n,) = struct.unpack("!I", head)
                chunks.append(n)
                if n == 0:
                    break
                body = _recv_exact(sock, n)
                if body is None:
                    srv.streams.append((bytes(data), chunks, False))
                    return
                data += body
                if srv.stream_max is not None and len(data) > srv.stream_max:
                    srv.streams.append((bytes(data), chunks, False))
                    if srv.abort_silently:
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                        return  # 不回任何字就 RST（clamd 當掉的樣子）
                    sock.sendall(b"INSTREAM size limit exceeded. ERROR\0")
                    # 半關閉後把客戶端剩下送的讀掉：直接 close 會因為收件匣還有資料而送 RST，
                    # 那一行 ERROR 可能還沒被讀到就被丟掉（真的 clamd 也可能如此，見 clamd.py docstring）。
                    sock.shutdown(socket.SHUT_WR)
                    sock.settimeout(2)
                    try:
                        while sock.recv(65536):
                            pass
                    except OSError:
                        pass
                    return
            srv.streams.append((bytes(data), chunks, True))
            if srv.scan_reply is not None:
                sock.sendall(srv.scan_reply)


class FakeClamd:
    def __init__(self):
        self.commands: list[str] = []
        self.streams: list[tuple[bytes, list[int], bool]] = []
        self.version_reply: str | None = version_line(timedelta(hours=3))
        self.scan_reply: bytes | None = b"stream: OK\0"
        self.stream_max: int | None = None
        self.abort_silently = False
        self.hang: dict[str, bool] = {}
        self.release = threading.Event()
        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        self.server.daemon_threads = True
        self.server.owner = self  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


class _Base(unittest.TestCase):
    def setUp(self):
        self.fake = FakeClamd()
        self.addCleanup(self.fake.close)
        self.cfg = clamd.ClamdConfig(host="127.0.0.1", port=self.fake.port, timeout=2.0)

    def scan(self, data=b"%PDF-1.7\n...%%EOF\n", **cfg):
        cfg_ = clamd.ClamdConfig(**{**self.cfg.__dict__, **cfg})
        return clamd.scan(data, cfg_, now=NOW)


class ProtocolTests(_Base):
    def test_ping(self):
        clamd.ping(self.cfg)
        self.assertEqual(self.fake.commands, ["zPING"])

    def test_ok_passes_and_carries_engine(self):
        r = self.scan()
        self.assertTrue(r.passed, r)
        self.assertEqual((r.status, r.signature, r.kind), ("ok", None, None))
        self.assertEqual(r.engine.engine, "1.4.3")
        self.assertEqual(r.engine.label, "ClamAV 1.4.3/27412/2026-10-06T09:00:00Z")
        self.assertEqual(self.fake.commands, ["zVERSION", "zINSTREAM"])

    def test_instream_is_chunked_length_prefixed_and_terminated(self):
        data = os.urandom(clamd.CHUNK_SIZE * 2 + 123)
        self.assertTrue(self.scan(io.BytesIO(data)).passed)
        received, chunks, terminated = self.fake.streams[0]
        self.assertEqual(received, data)
        self.assertEqual(chunks, [clamd.CHUNK_SIZE, clamd.CHUNK_SIZE, 123, 0])
        self.assertTrue(terminated)

    def test_found(self):
        self.fake.scan_reply = b"stream: Win.Test.EICAR_HDB-1 FOUND\0"
        r = self.scan()
        self.assertFalse(r.passed)
        self.assertEqual((r.status, r.signature), ("found", "Win.Test.EICAR_HDB-1"))
        self.assertFalse(r.heuristic)

    def test_alert_exceeds_max_is_a_heuristic_found_not_ok(self):
        self.fake.scan_reply = b"stream: Heuristics.Limits.Exceeded.MaxFileSize FOUND\0"
        r = self.scan()
        self.assertEqual(r.status, "found")
        self.assertTrue(r.heuristic)
        self.assertFalse(r.passed)

    def test_unknown_error_is_deterministic(self):
        self.fake.scan_reply = b"stream: Unexpected scan failure ERROR\0"
        r = self.scan()
        self.assertEqual((r.status, r.kind), ("error", "clamd_error"))
        self.assertFalse(r.transient)
        self.assertFalse(r.passed)

    def test_resource_error_is_transient(self):
        self.fake.scan_reply = b"stream: Can't allocate memory ERROR\0"
        r = self.scan()
        self.assertEqual(r.kind, "clamd_busy")
        self.assertTrue(r.transient)
        self.assertFalse(r.passed)

    def test_server_size_limit_is_deterministic_failure(self):
        """clamd 的 StreamMaxLength 先到：寫一行 ERROR 就關連線（客戶端可能還在送）。"""
        self.fake.stream_max = 100_000
        r = self.scan(os.urandom(600_000))
        self.assertEqual((r.status, r.kind), ("error", "size_limit"))
        self.assertIn("size limit exceeded", r.detail)
        self.assertFalse(r.transient)
        self.assertFalse(r.passed)

    def test_client_size_limit_never_returns_ok(self):
        """客戶端上限先到：即使 clamd 會回 OK，也不送收尾塊、直接判未通過。"""
        r = self.scan(os.urandom(5000), max_stream_bytes=1000)
        self.assertEqual(r.kind, "size_limit")
        self.assertFalse(r.transient)
        self.assertFalse(r.passed)
        deadline = time.monotonic() + 3
        while not self.fake.streams and time.monotonic() < deadline:
            time.sleep(0.02)
        _received, chunks, terminated = self.fake.streams[0]
        self.assertFalse(terminated)
        self.assertNotIn(0, chunks)

    def test_connection_dropped_mid_stream_is_transient(self):
        self.fake.stream_max = 100_000
        self.fake.abort_silently = True
        r = self.scan(os.urandom(600_000))
        self.assertIn(r.kind, ("connection_error", "protocol"))
        self.assertTrue(r.transient)
        self.assertFalse(r.passed)

    def test_garbage_and_empty_replies_are_transient_protocol_errors(self):
        for reply in (b"stream: MAYBE\0", b"", b"\0"):
            with self.subTest(reply=reply):
                self.fake.scan_reply = reply
                r = self.scan()
                self.assertEqual(r.kind, "protocol")
                self.assertTrue(r.transient)
                self.assertFalse(r.passed)


class UnavailableTests(_Base):
    def test_connection_refused(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        r = self.scan(port=port)
        self.assertEqual(r.kind, "connection_refused")
        self.assertTrue(r.transient)
        self.assertFalse(r.passed)
        with self.assertRaises(clamd.ClamdError) as cm:
            clamd.ping(clamd.ClamdConfig(port=port, timeout=2.0))
        self.assertTrue(cm.exception.transient)

    def test_timeout_waiting_for_scan_result(self):
        self.fake.hang["zINSTREAM"] = True
        r = self.scan(timeout=0.3)
        self.assertEqual(r.kind, "timeout")
        self.assertTrue(r.transient)
        self.assertFalse(r.passed)

    def test_timeout_on_version(self):
        self.fake.hang["zVERSION"] = True
        r = self.scan(timeout=0.3)
        self.assertEqual(r.kind, "timeout")
        self.assertNotIn("zINSTREAM", self.fake.commands)


class SignatureAgeTests(_Base):
    def test_stale_signatures_block_without_scanning(self):
        self.fake.version_reply = version_line(timedelta(hours=73))
        r = self.scan()
        self.assertEqual(r.kind, "signatures_stale")
        self.assertTrue(r.transient)
        self.assertFalse(r.passed)
        self.assertNotIn("zINSTREAM", self.fake.commands, "病毒碼過舊時不該掃（掃了也不能信）")

    def test_just_inside_the_threshold_scans(self):
        self.fake.version_reply = version_line(timedelta(hours=71, minutes=59))
        self.assertTrue(self.scan().passed)

    def test_threshold_comes_from_config(self):
        self.fake.version_reply = version_line(timedelta(hours=5))
        r = self.scan(max_signature_age=timedelta(hours=4))
        self.assertEqual(r.kind, "signatures_stale")

    def test_malformed_version_is_unknown_and_not_scanned(self):
        for reply in ("ClamAV 1.4.3", "ClamAV 1.4.3/27412/garbage", "ClamAV 1.4.3/27412/Mon Foo  5 08:30:42 2026",
                      "ClamAV 1.4.3/27412/Mon Feb 30 08:30:42 2026", "hello", ""):
            with self.subTest(reply=reply):
                self.fake.commands.clear()
                self.fake.version_reply = reply
                r = self.scan()
                self.assertIn(r.kind, ("signatures_unknown", "protocol"))
                self.assertTrue(r.transient)
                self.assertFalse(r.passed)
                self.assertNotIn("zINSTREAM", self.fake.commands)

    def test_future_date_is_unknown(self):
        """容器時區被改成 UTC+8 時日期會跑到未來：判斷不出來，不放行。"""
        self.fake.version_reply = version_line(-timedelta(hours=8))
        r = self.scan()
        self.assertEqual(r.kind, "signatures_unknown")
        self.assertFalse(r.passed)


class ParseVersionTests(unittest.TestCase):
    def test_parses_ctime_with_padded_day(self):
        v = clamd.parse_version("ClamAV 1.4.3/27412/Mon Oct  5 08:30:42 2026\n")
        self.assertEqual((v.engine, v.db_version), ("1.4.3", 27412))
        self.assertEqual(v.db_date, datetime(2026, 10, 5, 8, 30, 42, tzinfo=timezone.utc))
        self.assertEqual(v.age(NOW), NOW - v.db_date)

    def test_engine_without_database(self):
        v = clamd.parse_version("ClamAV 1.4.3")
        self.assertEqual((v.engine, v.db_version, v.db_date), ("1.4.3", None, None))
        self.assertIsNone(v.age(NOW))
        self.assertEqual(clamd.check_signatures(v, timedelta(hours=72), NOW).kind, "signatures_unknown")

    def test_unparseable(self):
        v = clamd.parse_version("not clamav")
        self.assertIsNone(v.engine)
        self.assertEqual(v.label, "not clamav")


class ResultSemanticsTests(unittest.TestCase):
    def test_only_ok_passes(self):
        self.assertTrue(clamd.ScanResult("ok").passed)
        self.assertFalse(clamd.ScanResult("found", signature="x").passed)
        for kind in clamd.TRANSIENT_KINDS | clamd.DETERMINISTIC_KINDS:
            with self.subTest(kind=kind):
                self.assertFalse(clamd.ScanResult("error", kind=kind).passed)

    def test_kind_sets_are_disjoint(self):
        self.assertFalse(clamd.TRANSIENT_KINDS & clamd.DETERMINISTIC_KINDS)
        self.assertIn("size_limit", clamd.DETERMINISTIC_KINDS)
        for kind in ("connection_refused", "timeout", "signatures_stale", "signatures_unknown"):
            self.assertIn(kind, clamd.TRANSIENT_KINDS)


class ConfigTests(unittest.TestCase):
    _KEYS = ("CLAMD_HOST", "CLAMD_PORT", "CLAMD_TIMEOUT", "CLAMD_SIGNATURE_MAX_AGE_HOURS", "CLAMD_STREAM_MAX_BYTES")

    def _load(self, **env):
        from app import config

        clean = {k: v for k, v in os.environ.items() if k not in self._KEYS}
        with mock.patch.dict(os.environ, {**clean, **env}, clear=True):
            return clamd.ClamdConfig.from_settings(config._load())

    def test_defaults(self):
        cfg = self._load()
        self.assertEqual((cfg.host, cfg.port, cfg.timeout), ("127.0.0.1", 3310, 120.0))
        self.assertEqual(cfg.max_signature_age, timedelta(hours=72))
        self.assertEqual(cfg.max_stream_bytes, 30 * 1024 * 1024)

    def test_overrides(self):
        cfg = self._load(CLAMD_HOST="10.0.0.5", CLAMD_PORT="3311", CLAMD_TIMEOUT="30",
                         CLAMD_SIGNATURE_MAX_AGE_HOURS="24", CLAMD_STREAM_MAX_BYTES="1000")
        self.assertEqual((cfg.host, cfg.port, cfg.timeout, cfg.max_stream_bytes), ("10.0.0.5", 3311, 30.0, 1000))
        self.assertEqual(cfg.max_signature_age, timedelta(hours=24))

    def test_invalid_values_fall_back_instead_of_disabling_the_gate(self):
        with self.assertLogs("app.config", level="WARNING"):
            cfg = self._load(CLAMD_PORT="70000", CLAMD_TIMEOUT="0", CLAMD_SIGNATURE_MAX_AGE_HOURS="nan",
                             CLAMD_STREAM_MAX_BYTES="-1")
        self.assertEqual((cfg.port, cfg.timeout, cfg.max_stream_bytes), (3310, 120.0, 30 * 1024 * 1024))
        self.assertEqual(cfg.max_signature_age, timedelta(hours=72))


if __name__ == "__main__":
    unittest.main()
