"""deploy/clamav/ 的契約：clamd 設定裡「預設值會靜默放行」的那幾項必須在，compose 不可對外或併進邊緣。

只讀檔案，不啟動容器、不 pull 映像。compose 以文字比對（不依賴 PyYAML：它只是間接相依）。
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

CLAMAV_DIR = REPO_ROOT / "deploy" / "clamav"
COMPOSE = CLAMAV_DIR / "docker-compose.yml"
CLAMD_CONF = CLAMAV_DIR / "conf" / "clamd.conf"
FRESHCLAM_CONF = CLAMAV_DIR / "conf" / "freshclam.conf"
EDGE_COMPOSE = REPO_ROOT / "deploy" / "docker-compose.yml"
MAKEFILE = REPO_ROOT / "Makefile"


def directives(path: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(" ")
        out.setdefault(key, []).append(value.strip())
    return out


def size_bytes(value: str) -> int:
    m = re.fullmatch(r"(\d+)([KM]?)", value)
    assert m, value
    return int(m.group(1)) * {"": 1, "K": 1024, "M": 1024 * 1024}[m.group(2)]


def compose_lines() -> list[str]:
    return [ln for ln in COMPOSE.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")]


class ClamdConfTests(unittest.TestCase):
    def test_fail_open_defaults_are_overridden(self):
        d = directives(CLAMD_CONF)
        self.assertEqual(d.get("AlertExceedsMax"), ["yes"], "預設會只掃一部分就回 OK")
        self.assertEqual(d.get("AlertEncrypted"), ["yes"], "預設把打不開的加密 PDF 當乾淨")
        self.assertEqual(d.get("ConcurrentDatabaseReload"), ["no"], "預設重載時記憶體翻倍，撞 mem_limit")

    def test_stream_limit_covers_the_upload_cap(self):
        d = directives(CLAMD_CONF)
        self.assertEqual(len(d.get("StreamMaxLength", [])), 1)
        limit = size_bytes(d["StreamMaxLength"][0])
        self.assertGreaterEqual(limit, 25 * 1024 * 1024, "設計決策 2：上傳上限 25 MB")
        # 單檔與單次掃描上限若小於串流上限，超過的部分要靠 AlertExceedsMax 才不會被當 OK
        for key in ("MaxFileSize", "MaxScanSize"):
            self.assertGreaterEqual(size_bytes(d[key][0]), limit, key)

    def test_entrypoint_requirements_are_present(self):
        """這份整個取代映像內建的 clamd.conf：entrypoint 等的 socket 與對外的 TCP 埠都要自己寫。"""
        d = directives(CLAMD_CONF)
        self.assertIn(d.get("LocalSocket", [""])[0], ("/tmp/clamd.sock", "/run/clamav/clamd.sock"))
        self.assertEqual(d.get("TCPSocket"), ["3310"])
        self.assertNotIn("TCPAddr", d, "容器內要聽所有介面，對外邊界在 compose 的 127.0.0.1 埠發佈")
        self.assertEqual(d.get("DatabaseDirectory"), ["/var/lib/clamav"])

    def test_freshclam_conf_is_complete(self):
        d = directives(FRESHCLAM_CONF)
        self.assertEqual(d.get("DatabaseDirectory"), ["/var/lib/clamav"])
        self.assertEqual(d.get("DatabaseOwner"), ["clamav"])
        self.assertTrue(d.get("DatabaseMirror"))
        self.assertEqual(d.get("NotifyClamd"), ["/etc/clamav/clamd.conf"])


class ComposeTests(unittest.TestCase):
    def test_image_is_the_official_one_pinned_to_a_minor(self):
        images = [ln.split("image:", 1)[1].strip() for ln in compose_lines() if ln.strip().startswith("image:")]
        self.assertEqual(len(images), 1)
        self.assertRegex(images[0], r"^clamav/clamav:\d+\.\d+$", "釘 minor（不可 latest／stable）")

    def test_only_loopback_port(self):
        text = "\n".join(compose_lines())
        ports = re.findall(r'^\s*-\s*"([^"]+:\d+)"\s*$', text, re.M)
        self.assertEqual(ports, ["127.0.0.1:3310:3310"])

    def test_not_on_the_edge_network(self):
        text = "\n".join(compose_lines())
        self.assertNotIn("networks:", text)
        self.assertNotIn("edge", text)
        self.assertNotIn("clamav", EDGE_COMPOSE.read_text(encoding="utf-8").lower(),
                         "不可併進 deploy/docker-compose.yml（edge-reload 會牽動它）")

    def test_runtime_settings(self):
        text = "\n".join(compose_lines())
        self.assertRegex(text, r"(?m)^\s+container_name: report-mark-clamav$")
        self.assertRegex(text, r"(?m)^\s+restart: unless-stopped$")
        self.assertRegex(text, r"(?m)^\s+mem_limit: 2g$")
        self.assertRegex(text, r"(?m)^\s+TZ: Etc/UTC$", "clamd.py 把病毒碼日期當 UTC")
        self.assertRegex(text, r"(?m)^\s+- clamav-db:/var/lib/clamav$", "病毒碼放 named volume")
        self.assertRegex(text, r"(?m)^\s+- \./conf:/etc/clamav:ro$", "掛目錄不掛單檔，唯讀")
        self.assertNotRegex(text, r"CLAMD_CONF_|FRESHCLAM_CONF_", "entrypoint 會 sed -i 唯讀掛載的設定")


class MakefileTests(unittest.TestCase):
    def test_targets_use_the_detected_compose(self):
        text = MAKEFILE.read_text(encoding="utf-8")
        self.assertIn("CLAMAV_COMPOSE ?= deploy/clamav/docker-compose.yml", text)
        self.assertRegex(text, r"(?m)^up-clamav:.*\n\t\$\(COMPOSE\) -f \$\(CLAMAV_COMPOSE\) up -d$")
        self.assertRegex(text, r"(?m)^down-clamav:.*\n\t\$\(COMPOSE\) -f \$\(CLAMAV_COMPOSE\) down$",
                         "down 不帶 -v：病毒碼 volume 要留著")


if __name__ == "__main__":
    unittest.main()


class ClientConsistencyTests(unittest.TestCase):
    def test_client_stream_cap_matches_clamd_conf(self):
        """客戶端上限與 StreamMaxLength 一致：客戶端先到時直接判超限，不賭 clamd 斷線前那一行 ERROR 讀不讀得到。"""
        from app.config import Settings

        conf_limit = size_bytes(directives(CLAMD_CONF)["StreamMaxLength"][0])
        self.assertEqual(Settings.__dataclass_fields__["clamd_stream_max_bytes"].default, conf_limit)

    def test_client_defaults_match_compose(self):
        from app.config import Settings

        fields = Settings.__dataclass_fields__
        self.assertEqual(fields["clamd_host"].default, "127.0.0.1")
        self.assertEqual(fields["clamd_port"].default, 3310)
        self.assertEqual(fields["clamd_signature_max_age_hours"].default, 72.0)


class SmokeScriptTests(unittest.TestCase):
    def test_eicar_is_assembled_at_runtime_and_correct(self):
        import hashlib

        from scripts import clamav_smoke

        data = clamav_smoke.eicar()
        self.assertEqual(hashlib.md5(data).hexdigest(), "44d88612fea8a8f36de82e1278abb02f")  # EICAR 官方 MD5
        # 完整字串不可原樣出現在 repo 的任何檔案（防毒隔離、secret 掃描誤報）
        for path in [*REPO_ROOT.glob("scripts/*.py"), *REPO_ROOT.glob("tests/*.py"), *REPO_ROOT.glob("app/**/*.py"),
                     MAKEFILE, *CLAMAV_DIR.rglob("*")]:
            if path.is_file():
                with self.subTest(path=path.name):
                    self.assertNotIn(data, path.read_bytes())

    def test_minimal_pdf_is_a_valid_pdf(self):
        import io

        from pypdf import PdfReader

        from scripts import clamav_smoke

        pdf = clamav_smoke.minimal_pdf()
        self.assertTrue(pdf.startswith(b"%PDF-1."))
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))
        reader = PdfReader(io.BytesIO(pdf), strict=True)
        self.assertEqual(len(reader.pages), 1)
        self.assertIn("clamav smoke", reader.pages[0].extract_text())

    def test_unreachable_clamd_exits_2(self):
        import contextlib
        import io
        import socket
        from unittest import mock

        from app.services import clamd
        from scripts import clamav_smoke

        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        cfg = clamd.ClamdConfig(port=port, timeout=2.0)
        out = io.StringIO()
        with mock.patch.object(clamd.ClamdConfig, "from_settings", return_value=cfg), \
                contextlib.redirect_stdout(out):
            self.assertEqual(clamav_smoke.main([]), 2)
        self.assertIn("連不上 clamd", out.getvalue())

    def test_makefile_target(self):
        text = MAKEFILE.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^clamav-smoke:.*\n\tuv run python scripts/clamav_smoke\.py ")
