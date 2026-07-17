"""研報閱讀頁服務層：正典文字、錨點、查詢。讀取時零 LLM。"""

from app.services.reading.anchor import Anchor, locate_chunk, locate_quote

__all__ = ["Anchor", "locate_chunk", "locate_quote"]
