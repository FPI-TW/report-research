"""授權守門：不得引入帶「網路條款」的 copyleft 相依（AGPL／SSPL）。

本站經 Cloudflare Tunnel 對外服務。AGPL-3.0 §13 與 SSPL 的條款在「經網路提供服務」時即
觸發，後果是整個服務的原始碼都要以同一授權釋出。`pyproject.toml` 與 `docs/EXTRACTION.md` §2
記錄了因此**刻意不用 PyMuPDF** 的決定，但在這支測試之前那條約束純靠人記得——下一個人加
相依、或某個既有相依在升版時換了授權，不會有任何東西變紅。

三道檢查，互補：

1. 已安裝套件的授權 metadata（涵蓋遞移相依；`uv sync` 裝的就是生產跑的那一組）。
2. `uv.lock` 的套件名黑名單（涵蓋因平台標記而沒裝在這台機器上的）。
3. `frontend/package-lock.json` 每個套件的 `license` 欄位。

**範圍刻意只到網路條款類**。一般 GPL 不因 SaaS 使用而觸發（沒有散布），目前環境裡確實有
GPLv2 的遞移相依（`warc3-wet`，FlagEmbedding 帶進來的），那不是這支測試要擋的東西。

紅了的處置：先確認授權判讀無誤，再換掉那個相依。**不要加進豁免清單來讓它變綠**——
豁免清單只給「metadata 寫錯、實際授權已查證」的情況用，且要在條目旁寫明查證依據。
"""

from __future__ import annotations

import importlib.metadata as md
import json
import re
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]

# Affero GPL 與 SSPL。用字串比對而不解析 SPDX：metadata 的寫法五花八門
# （"AGPL-3.0-or-later"、"GNU Affero General Public License v3"、classifier 全名）。
NETWORK_COPYLEFT = re.compile(r"affero|\bagpl|\bsspl\b|server side public licen[sc]e", re.IGNORECASE)

# 名稱黑名單（PEP 503 正規化後比對）。PyMuPDF 系列是 AGPL／商用雙授權。
DENIED_PYTHON_PACKAGES = frozenset({"pymupdf", "pymupdfb", "pymupdf4llm", "pymupdfpro"})

# metadata 有誤、實際授權已人工查證者。格式：正規化套件名 → 查證依據。目前為空。
VERIFIED_EXEMPTIONS: dict[str, str] = {}


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# `License` 是自由文字欄位：多數套件放識別字（"MIT"），但有些把授權**全文**塞進去。
# scipy 放了 4.7 萬字（含它打包的 GPLv3 元件條文），而 GPLv3 第 13 條本身就提到
# "GNU Affero General Public License"——整段比對會誤判。超過這個長度就只看第一行。
_LICENSE_TEXT_MAX = 200


def _license_fields(dist: md.Distribution) -> list[str]:
    meta = dist.metadata
    free_text = (meta.get("License") or "").strip()
    if len(free_text) > _LICENSE_TEXT_MAX:
        free_text = free_text.splitlines()[0][:_LICENSE_TEXT_MAX]
    fields = [meta.get("License-Expression") or "", free_text]
    fields += [c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
    return [f for f in fields if f]


class PatternTests(unittest.TestCase):
    """守門本身要先對：比對式抓得到該抓的、不誤傷 LGPL／GPL。"""

    def test_matches_network_copyleft_spellings(self):
        for text in (
            "AGPL-3.0-or-later", "AGPL-3.0-only", "GNU AFFERO GPL 3.0",
            "License :: OSI Approved :: GNU Affero General Public License v3",
            "SSPL-1.0", "Server Side Public License",
            "Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License",
        ):
            self.assertRegex(text, NETWORK_COPYLEFT)

    def test_does_not_match_other_licenses(self):
        for text in (
            "MIT", "Apache-2.0", "BSD-3-Clause", "LGPL-3.0-or-later", "GPLv2",
            "License :: OSI Approved :: GNU General Public License v2 (GPLv2)",
            "License :: OSI Approved :: GNU Lesser General Public License v2 or later (LGPLv2+)",
        ):
            self.assertNotRegex(text, NETWORK_COPYLEFT)


class PythonLicenseGuardTests(unittest.TestCase):
    def test_full_license_text_in_metadata_is_not_scanned_whole(self):
        """GPLv3 條文自己就提到 Affero；把全文塞進 License 欄位的套件不該因此被判違規。"""

        def fake(license_text: str):
            msg = Message()
            msg["Name"] = "fake"
            msg["License"] = license_text
            return SimpleNamespace(metadata=msg)

        def hits(dist):
            return [f for f in _license_fields(dist) if NETWORK_COPYLEFT.search(f)]

        gpl_clause = "13. Use with the GNU Affero General Public License."
        self.assertEqual(hits(fake("BSD-3-Clause\n" + "x" * 300 + "\n" + gpl_clause)), [])
        # 對照：識別字就寫在第一行的，全文再長也抓得到。
        self.assertEqual(len(hits(fake("GNU AFFERO GPL 3.0\n" + "x" * 300))), 1)

    def test_no_installed_distribution_is_network_copyleft(self):
        offenders = {}
        seen = 0
        for dist in md.distributions():
            name = dist.metadata.get("Name")
            if not name:
                continue
            seen += 1
            if _normalize(name) in VERIFIED_EXEMPTIONS:
                continue
            hits = [f[:120] for f in _license_fields(dist) if NETWORK_COPYLEFT.search(f)]
            if hits:
                offenders[name] = hits
        # 掃到的套件數是否合理：環境是空的（或 metadata 讀不到）時，「零違規」沒有意義。
        self.assertGreater(seen, 50, "掃到的已安裝套件太少，這支測試等於沒跑")
        self.assertEqual(offenders, {}, "引入了帶網路條款的 copyleft 相依（處置見本檔 docstring）")

    def test_lockfile_has_no_denied_package(self):
        lock = (REPO_ROOT / "uv.lock").read_text(encoding="utf-8")
        names = {_normalize(n) for n in re.findall(r'^name = "([^"]+)"', lock, flags=re.MULTILINE)}
        self.assertGreater(len(names), 50, "uv.lock 解析不到套件名，這支測試等於沒跑")
        self.assertEqual(names & DENIED_PYTHON_PACKAGES, set())


class FrontendLicenseGuardTests(unittest.TestCase):
    def test_no_locked_package_is_network_copyleft(self):
        lock = json.loads((REPO_ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
        packages = {k: v for k, v in lock.get("packages", {}).items() if k}
        self.assertGreater(len(packages), 50, "package-lock.json 解析不到套件，這支測試等於沒跑")
        offenders = {
            path: str(info.get("license"))
            for path, info in packages.items()
            if NETWORK_COPYLEFT.search(str(info.get("license") or ""))
        }
        self.assertEqual(offenders, {}, "前端引入了帶網路條款的 copyleft 相依")


if __name__ == "__main__":
    unittest.main()
