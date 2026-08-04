"""研報閱讀頁服務層：正典文字、錨點、查詢。讀取時零 LLM。"""

from app.services.reading.anchor import Anchor, locate_chunk, locate_quote
from app.services.reading.queries import (
    DocRow,
    SimilarRow,
    TakeawayRow,
    fetch_chunk_content,
    fetch_doc,
    fetch_instrument_names,
    fetch_signals,
    fetch_similar,
    fetch_takeaways,
)

__all__ = [
    "Anchor",
    "DocRow",
    "SimilarRow",
    "TakeawayRow",
    "fetch_chunk_content",
    "fetch_doc",
    "fetch_instrument_names",
    "fetch_signals",
    "fetch_similar",
    "fetch_takeaways",
    "locate_chunk",
    "locate_quote",
]
