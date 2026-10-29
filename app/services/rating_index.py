"""從送給 LLM 的研報內文剔除「近期報告／評等異動一覽表」。

## 這支模組在修什麼

晨報與日報的封面常印一張「過去幾週發過哪些報告、給了什麼評等」的索引表，每天照抄、
只往下推：

    元大投資早報   10/15 2049上銀 買進
    凱基投資早報   07/15 台積電(2330) 重啟評等 增加持股
    凱基產業報告   8/23 富世達 6805 增加持股 1,000 伺服器為2025年營運亮點

這些列不是本篇的內容，卻長得最像「個股＋評等」。2026-10 全庫盤點（測試環境）：

- 標註把索引表裡的代碼當成本篇標的，`stock_targets` 的 8 檔上限被舊報告佔滿，內文真正
  討論的個股反而被擠掉（例：元大 2026-10-22 早報 8 檔裡 7 檔來自索引表）。
- 訊號擷取把索引列當成本篇評等，436 筆 `report_signal` 的評等證據是索引列，報告日卻是
  轉載當天——07/15 的台積電重啟評等因此在數十份凱基早報各記一次，雷達與簡報的評等變動
  被重複灌入。

## 判定規則（量出來的，改之前先重跑盤點）

一列算「索引列」＝行首是 `M/D` 日期，且含評等詞；或以 ` -` 結尾（元大的無評等列，例如
`10/20 生技製藥產業 -`），而中間幾乎沒有數字欄。兩列索引列之間夾的非空行不超過 `MAX_GAP`
就算同一張表（雙欄版面會把另一欄的片段夾進來，空行不算），同一張表至少 `MIN_ROWS` 列才
剔除，而且其中至少要有一列帶評等詞；表的範圍內所有日期開頭的列一起拿掉。

只看「日期開頭」會誤傷：大和的年度財測表（`3/22 401,138 …`，EPS 擷取要用）、經濟行事曆、
圖表座標都長這樣，所以一定要評等詞；以 ` -` 結尾的列再擋數字欄，是因為大和財測表的股利欄
也是 `-`；「至少一列帶評等詞」擋的是富邦週報的選舉行事曆（`3/10 葡萄牙 議會選舉 -`，整群都是 `-` 結尾）。
2026-10 盤點命中 1,075 篇、只有元大與凱基；單篇最多剔除 3.9% 的字。

凱基 Taiwan daily 的封面是雙欄，索引列右半常黏著另一欄的內文片段，整列拿掉會連帶丟掉
那幾個字。這裡只影響送 LLM 的標註與訊號輸入，可以接受。

抓不到的：表格被抽成一欄一欄（日期與公司一欄、評等另一欄）時，原文裡沒有完整的索引列，
LLM 卻會自己拼回去（凱基 Radar Monthly）；孤立的單列評等異動也不成群。2026-10 盤點這類約
占索引表訊號的 6%，由修復批次 `scripts/repair_rating_index.py` 以證據本身的長相補刪。

## 只用在哪裡

標註（`scripts/_ingest_core.py`、`scripts/tag_all_cli.py`）與訊號擷取
（`scripts/extract_signals.py`）在截取前 N 字之前先剔除——順帶讓截取額度不被索引表吃掉。
`full_text`、切塊、嵌入、閱讀頁都不動：索引表對讀者仍是報告的一部分。

失效模式 fail-open：剔除後剩不到 `MIN_KEEP_FRAC` 就退回原文。
"""

from __future__ import annotations

import re

MIN_ROWS = 3
MAX_GAP = 3
MIN_KEEP_FRAC = 0.5

_DATE_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})(?=\s|$)")
_RATING_RE = re.compile(
    r"買進|持有|賣出|中立|增加持股|降低持股|減少持股|首次評等|重啟評等|未評等|評估中|限制評等"
    r"|優於大盤|落後大盤"
    r"|\b(?:Buy|Hold|Sell|Neutral|Outperform|Underperform|Overweight|Underweight|Equal-?weight)\b",
    re.IGNORECASE,
)
_NUMERIC_TOKEN_RE = re.compile(r"^[\d,.()%+\-▲▼]+$")


def _is_date_line(line: str) -> bool:
    m = _DATE_RE.match(line)
    return bool(m) and 1 <= int(m.group(1)) <= 12 and 1 <= int(m.group(2)) <= 31


def is_index_row(line: str) -> bool:
    """單看一列像不像索引列（規則見檔頭；還要成群才剔除，見 `index_line_numbers`）。"""
    if not _is_date_line(line):
        return False
    if _RATING_RE.search(line):
        return True
    stripped = line.rstrip()
    if not stripped.endswith(" -"):
        return False
    middle = stripped.split()[1:-1]
    return bool(middle) and sum(1 for tok in middle if _NUMERIC_TOKEN_RE.match(tok)) <= 1


def index_line_numbers(text: str) -> set[int]:
    """回傳屬於索引表、要剔除的行號（以 `text.split("\\n")` 計，0 起算）。"""
    lines = text.split("\n")
    clusters: list[list[int]] = []
    between = 0  # 上一列索引列之後夾了幾行非空的其他文字
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if not is_index_row(line):
            between += 1
            continue
        if clusters and between <= MAX_GAP:
            clusters[-1].append(i)
        else:
            clusters.append([i])
        between = 0
    drop: set[int] = set()
    for rows in clusters:
        # 整群都是「-」結尾、沒有一列帶評等詞的是行事曆（富邦週報的「3/10 葡萄牙 議會選舉 -」），不是索引表
        if len(rows) < MIN_ROWS or not any(_RATING_RE.search(lines[i]) for i in rows):
            continue
        drop.update(i for i in range(rows[0], rows[-1] + 1) if _is_date_line(lines[i]))
    return drop


def strip_rating_index(text: str | None) -> str:
    """剔除近期報告／評等異動索引表後的內文（規則見檔頭）；None 視為空字串。"""
    if not text:
        return text or ""
    drop = index_line_numbers(text)
    if not drop:
        return text
    kept = "\n".join(line for i, line in enumerate(text.split("\n")) if i not in drop)
    if len(kept) < len(text) * MIN_KEEP_FRAC:
        return text
    return kept


def index_lines(text: str | None) -> list[str]:
    """索引表裡被剔除的那些列（`strip_rating_index` 退回原文時一樣回傳）。"""
    if not text:
        return []
    lines = text.split("\n")
    return [lines[i] for i in sorted(index_line_numbers(text))]


def evidence_from_index(evidence: str | None, code: str, lines: list[str]) -> bool:
    """訊號的評等證據是否抄自索引列：同一日期，且該列含代碼或證據的第一個詞。

    LLM 回的證據會補字（`(6488` 寫成 `(6488 TT)`），也會把下一行的代碼併進來（凱基雙欄封面
    `03/31 台新新光金 持有 增加持股` 的 `(2887)` 在下一行），所以不比全文、只比日期加一個錨點。
    """
    m = _DATE_RE.match(evidence or "")
    if not m:
        return False
    date_token = m.group(0).strip()
    rest = (evidence or "")[m.end():].split()
    anchors = [code] + rest[:1]
    for line in lines:
        lm = _DATE_RE.match(line)
        if lm and lm.group(0).strip() == date_token and any(a and a in line for a in anchors):
            return True
    return False
