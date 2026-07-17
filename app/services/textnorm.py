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
    # NUL（\x00）是 Postgres text/UTF8 不合法位元組（部分 GS/KGI PDF 帶私用區字符會夾帶），
    # 入庫前一律剝除，否則 upsert chunk 會拋 CharacterNotInRepertoireError 而永久失敗。
    s = s.replace("\x00", "")
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


def norm_for_match_with_map(s: str) -> tuple[str, list[int]] | None:
    """norm_for_match 的可回溯版：同時回傳每個正規化字元的原始 index。

    回傳 `(norm, idx)`，其中 `norm[i]` 來自 `s[idx[i]]`；供
    `app/services/reading/anchor.py` 把正規化後找到的位置映射回原文 offset。

    **逐字元 NFKC 與整串 NFKC 不保證等價**：組合字元序列（如 か + ゛→ が）在整串
    正規化時會合併、逐字元時不會。故本函式建完後與 `norm_for_match(s)` 實際比對，
    不符即回 None，由呼叫端放棄錨定 —— 寧可不能跳，也不要跳到錯的地方。
    """
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(s):
        for c in unicodedata.normalize("NFKC", ch).lower():
            # NFKC 可能把 NBSP 之類轉成一般空白，故正規化「之後」才濾
            if _RE_ALL_WS.fullmatch(c):
                continue
            out.append(c)
            idx.append(i)
    norm = "".join(out)
    if norm != norm_for_match(s):
        return None
    return norm, idx
