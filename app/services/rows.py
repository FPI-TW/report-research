"""檢索結果的型別化 row。

`store.search_chunks_meta` / `search_chunks_lexical` 的 SELECT 欄位（見
`store._meta_columns`，15 欄）＋ 尾端 distance ＝ 16 欄。ChunkRow 為 NamedTuple
（tuple 子型），故既有位移存取（row[0]/row[-1]/row[-2] 等）與新的具名存取並存，
遷移期零回歸。
"""

from typing import NamedTuple


class ChunkRow(NamedTuple):
    chunk_id: str
    report_id: str
    file_name: str | None
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
