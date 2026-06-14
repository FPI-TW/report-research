"""文字正規化：顯示清理、入庫清理、比對正規化的單一事實來源。"""

from __future__ import annotations

import re
import unicodedata

# 清理 PDF 抽出的雜亂排版：CJK 字元間的換行/空白移除、其餘空白收斂
_CJK = r"一-鿿　-〿＀-￯"
_RE_CJK_GAP = re.compile(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])")
_RE_WS = re.compile(r"[ \t]{2,}")
_RE_ALL_WS = re.compile(r"\s+")
_RE_PARA = re.compile(r"\n\s*\n")


def clean_text(s: str) -> str:
    """顯示用清理：移除 CJK 間空白、收斂其餘空白、折疊換行。冪等。"""
    s = _RE_CJK_GAP.sub("", s)
    s = _RE_WS.sub(" ", s)
    s = re.sub(r"\s*\n\s*", " ", s)
    return s.strip()


def clean_extracted(s: str) -> str:
    """入庫前清理：逐段移除 CJK 間空白，保留段落邊界（chunk_text 依 \\n\\n 切段）。"""
    paras = _RE_PARA.split(s)
    return "\n\n".join(
        _RE_WS.sub(" ", _RE_CJK_GAP.sub("", p)).strip() for p in paras if p.strip()
    )


def norm_for_match(s: str) -> str:
    """比對用正規化：NFKC → 小寫 → 移除所有空白。

    必須與 db/schema.sql 的 content_norm generated column 表達式一致：
    lower(regexp_replace(normalize(content, NFKC), '\\s+', '', 'g'))
    """
    return _RE_ALL_WS.sub("", unicodedata.normalize("NFKC", s).lower())
