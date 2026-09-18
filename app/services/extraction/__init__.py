"""版面感知抽取層。

生產路徑由 `app/services/extract.py` 的 `EXTRACTOR=pdfplumber` 分支進來（sync 鏈的環境檔
自 2026-09-03 起如此設定），`scripts/profile_corpus.py` 與 `scripts/compare_extractors.py`
另外拿它做只讀比對，對應 docs/EXTRACTION.md §4。

**刻意不在此處 import 子模組**：`layout` 會拉進 pdfplumber／pdfminer.six
（連帶 cryptography），而 web 服務完全用不到。Python 相依是惰性載入的，
在 `__init__` 提上來等於讓每個 `from app.services import ...` 都付這筆成本。
"""

from __future__ import annotations

# 抽取器與版本識別，寫進 research_report.extractor / extraction_version（E1）。
# **改動 layout.py 的排序或分類邏輯、或 model.py 的序列化，就要 bump 這個字串**，
# 否則同一份 PDF 在不同版本下產生的 full_text 不同、而 DB 說它們是同一版。
EXTRACTOR_NAME = "pdfplumber"
# v3（2026-09-02）：溝槽投影容許少量跨欄詞、窄欄併回鄰欄、頁首重複首次保留。
# v4（2026-09-18）：相鄰詞合併（數字不再被填充空白切開）、分行改垂直中點分群、窄數值側欄併回
#   標籤欄、被段落穿過的溝槽剔除、框線表殘缺／欄位不足時以詞重建、圖區內刻度圖例不進正文、
#   quality_score 在 coverage 缺席時重正規化。回填腳本以此字串挑候選：bump 即全庫重排。
EXTRACTION_VERSION = "ext-2026-09-18.v4"

__all__ = ["EXTRACTOR_NAME", "EXTRACTION_VERSION"]
