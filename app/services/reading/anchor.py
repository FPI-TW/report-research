"""把 chunk／LLM 引文確定性地錨回全文的字元區間。閱讀頁「跳到那一段」的地基。

════════════════════════════════════════════════════════════════════════
改動本檔前必讀：兩個實測事實（2026-07-17 直接查生產 DB）
════════════════════════════════════════════════════════════════════════

**事實一：`research_report.full_text` 存的是「未清理」的原始抽取文字。**

`scripts/ingest_all.py` 寫入時：

    raw_text = (rec.get("text") or "").replace("\\x00", "")
    chunks = chunk_text(clean_extracted(raw_text))   # chunk 走「清理後」
    full_text=raw_text,                              # 欄位存「原始」

故 full_text 保留 PDF 抽字的 CJK 字元間空白（「台 積 電 第 三 季」）與破碎換行。
**閱讀頁的正典文字＝`clean_extracted(full_text)`，不是 `full_text`。**
本模組所有 offset 都錨定於正典文字，呼叫端務必傳入同一個字串。

**事實二：`report_chunk.content` 不是全文的子字串。**

抽樣 400 筆實測：

    未正規化直接比對 ....................  3/300（1%）   ← 被事實一的空白差異擋住
    正規化後整塊比對 .................... 168/400（42%）  ← 被 overlap 尾巴擋住
    正規化 + 丟掉開頭 CHUNK_OVERLAP 字 ... 400/400（100%）← 本模組採用
    正規化 + 中段 120 字探針 ............ 400/400（100%）← 退讓路徑

成因是 `app/services/chunk.py` 的 overlap 合併 `f"{tail}{cur}"`：它把前一塊末
CHUNK_OVERLAP 字**複製**到下一塊開頭；長段落又已是滑動視窗切分
（`start += size - overlap`）。結果同一段文字連續出現兩次 —— 這種字串在全文裡
根本不存在。丟掉 chunk 開頭那段注入的尾巴即可定位。

**天真的 `full_text.find(chunk.content)` 有 99% 機率無聲失敗。** 這也是為什麼
LLM 一律餵正典文字（不是 chunks）：引文與被搜文本同源就繞開整個問題。
"""

from __future__ import annotations

from typing import NamedTuple

from app.services.chunk import CHUNK_OVERLAP
from app.services.textnorm import norm_for_match, norm_for_match_with_map

# 引文短於此長度不錨定：太短的字串在一份研報裡幾乎必然多處出現（頁首、目錄、
# 表格標題），錨到哪一處都是猜的。寧可讓該條摘錄不能跳。
MIN_QUOTE_CHARS = 12

# 整段引文找不到時的前綴退讓長度（正規化後字元數，由長到短）
PREFIX_STEPS = (48, 32, 20)

# chunk 丟掉 overlap 後至少要剩這麼多字才用；否則退回整塊
MIN_PROBE_CHARS = 40

# 中段探針長度（丟 overlap 仍找不到時的最後一搏）
MID_PROBE_CHARS = 120


class Anchor(NamedTuple):
    """正典文字上的字元區間。`method` 記錄用哪一層退讓找到的，供稽核錨定品質。"""

    start: int
    end: int
    method: str  # exact | normalized | prefix


def _find_unique(hay: str, needle: str) -> int | None:
    """只在恰好出現一次時回傳位置。多處出現＝無法裁決 → None（寧缺勿錯）。"""
    if not needle:
        return None
    first = hay.find(needle)
    if first == -1:
        return None
    if hay.find(needle, first + 1) != -1:
        return None
    return first


def _to_orig(idx: list[int], n_start: int, n_len: int) -> tuple[int, int]:
    """正規化區間 → 原始字元區間 [start, end)。"""
    start = idx[n_start]
    end = idx[n_start + n_len - 1] + 1
    return start, end


def locate_quote(text: str, quote: str) -> Anchor | None:
    """把 LLM 回的逐字引文錨回正典文字。找不到、或找到多處，一律回 None。

    `text` 必須是 `clean_extracted(full_text)`（見模組 docstring 事實一）。

    三層退讓：精確 → 正規化 → 正規化前綴。LLM 常會「順手」改標點或補空白，
    正規化層就是為了吸收這類漂移。
    """
    if not text or not quote:
        return None

    # 1) 精確
    pos = _find_unique(text, quote)
    if pos is not None:
        return Anchor(pos, pos + len(quote), "exact")
    if text.find(quote) != -1:
        # 出現多處 → 無法裁決。正規化層只會找到同樣的多處，直接放棄。
        return None

    # 2) 正規化
    built = norm_for_match_with_map(text)
    if built is None:
        return None
    ntext, idx = built
    nq = norm_for_match(quote)
    if len(nq) < MIN_QUOTE_CHARS:
        return None

    pos = _find_unique(ntext, nq)
    if pos is not None:
        return Anchor(*_to_orig(idx, pos, len(nq)), "normalized")

    # 3) 前綴退讓：LLM 引文尾巴常被截斷或改寫，開頭通常還是逐字的
    for k in PREFIX_STEPS:
        if len(nq) <= k:
            continue
        pos = _find_unique(ntext, nq[:k])
        if pos is not None:
            return Anchor(*_to_orig(idx, pos, k), "prefix")

    return None


def locate_chunk(
    text: str, chunk_content: str, overlap: int = CHUNK_OVERLAP
) -> Anchor | None:
    """把檢索命中的 chunk 錨回正典文字，供「跳到命中那一段」。

    `text` 必須是 `clean_extracted(full_text)`（見模組 docstring 事實一）。

    先丟掉 `chunk.py` 注入的 overlap 尾巴再比對 —— 這是實測 400/400 命中的關鍵，
    不是可有可無的優化（保留尾巴只有 42%，見模組 docstring 事實二）。

    與 `locate_quote` 不同，此處取「第一個」出現位置而非要求唯一：chunk 正文
    數百字，在同一份研報內重複出現不切實際，而要求唯一反而會讓罕見的樣板段落
    （免責聲明、頁首）整個定位失敗。
    """
    if not text or not chunk_content:
        return None

    built = norm_for_match_with_map(text)
    if built is None:
        return None
    ntext, idx = built
    nc = norm_for_match(chunk_content)
    if not nc:
        return None

    # 1) 丟掉 overlap 尾巴
    probe = nc[overlap:] if len(nc) > overlap + MIN_PROBE_CHARS else nc
    pos = ntext.find(probe)
    if pos != -1:
        return Anchor(*_to_orig(idx, pos, len(probe)), "normalized")

    # 2) 中段探針：極端情況（chunk 跨越被 clean_extracted 丟棄的空段）的最後一搏
    if len(nc) > MID_PROBE_CHARS:
        mid = max(0, (len(nc) - MID_PROBE_CHARS) // 2)
        probe = nc[mid : mid + MID_PROBE_CHARS]
        pos = ntext.find(probe)
        if pos != -1:
            return Anchor(*_to_orig(idx, pos, len(probe)), "normalized")

    return None
