"""版面感知抽取層（唯讀 spike 階段）。

本套件目前**不接任何生產路徑**：`app/services/extract.py` 仍是 pypdf，
`scripts/{extract_all,sync_new_reports,ingest_all}.py` 一個字都沒改。
這裡的三支模組供 `scripts/profile_corpus.py` 與 `scripts/compare_extractors.py`
做「只讀不寫的並排比對」用，對應 docs/EXTRACTION_REDESIGN.md §9 的第 1、3 步。

**刻意不在此處 import 子模組**：`layout` 會拉進 pdfplumber／pdfminer.six
（連帶 cryptography），而 web 服務完全用不到。Python 相依是惰性載入的，
在 `__init__` 提上來等於讓每個 `from app.services import ...` 都付這筆成本。
"""

from __future__ import annotations

# 抽取器與版本識別，寫進 research_report.extractor / extraction_version（E1）。
# **改動 layout.py 的排序或分類邏輯、或 model.py 的序列化，就要 bump 這個字串**，
# 否則同一份 PDF 在不同版本下產生的 full_text 不同、而 DB 說它們是同一版。
EXTRACTOR_NAME = "pdfplumber"
EXTRACTION_VERSION = "ext-2026-09-02.v2"  # v2：頁首帶重複文字首次出現保留原型別

__all__ = ["EXTRACTOR_NAME", "EXTRACTION_VERSION"]
