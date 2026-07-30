"""檢索結果的型別化 row。

`store.search_chunks_meta` / `search_chunks_lexical` 的 SELECT 欄位（見
`store._meta_columns`）＋ 尾端 distance ＝ 本 NamedTuple 的全部欄位（欄數請看
`ChunkRow._fields`，不要抄數字——這裡原本寫死「16＋1＝17」，`title` 加進來之後就錯了）。
ChunkRow 為 NamedTuple（tuple 子型），故位移存取與具名存取並存。

`search_chunks_lexical` 的 SQL 另有一個**在 distance 之後**的 `lex_hits` 欄（cap 截斷
可觀測），它刻意不是 ChunkRow 的一部分、由該函式獨立回傳——理由同下。

**欄序須與 `store._meta_columns` 逐欄對齊**：`_make()` 純靠位置打包，錯位不會拋錯、
只會靜默給錯值。**新增欄位一律插在中段、絕不 append 到尾端**，且插欄後必須確認沒有
消費端寫死索引 —— `scripts/eval_retrieval.py` 原本寫死 `_RID, _CONTENT = 1, 14`，
插入 file_hash 後那兩個常數無聲指向錯的欄位，沒有例外、只有變成垃圾的評測分數
（現已改為 `ChunkRow._fields.index(...)`，新程式碼一律照做、不要寫數字）。
"""

from typing import NamedTuple


class ChunkRow(NamedTuple):
    chunk_id: str
    report_id: str
    file_hash: str  # 閱讀頁網址鍵（report_id 於重新 ingest 會換新）
    file_name: str | None
    title: str | None  # 報告內部標題（顯示用；NULL＝尚未產生，呼叫端回退 file_name）
    market: str | None
    source: str | None
    summary: str | None
    report_date: object  # date / datetime / str / None（沿用現況多型）
    report_type: str | None
    instrument_types: object
    relates_stock: object
    relates_futures: object
    stock_targets: object
    futures_targets: object
    chunk_index: int
    content: str
    distance: float
