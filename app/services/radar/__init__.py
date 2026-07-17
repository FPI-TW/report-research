"""觀點雷達服務層：純 SQL 取訊號 + Python 決定性聚合/比較（讀取時不呼叫 LLM）。

- types：Signal / EpsEstimate / DimensionStance 值型別 + jsonb→dataclass 解析
- queries：純 SQL 取訊號、覆蓋度、標的目錄
- scale：評等尺度、四分位、pct、stance 建設性、四維分類、單券商差異（純函式）
- compute：build_overview / build_broker_history 聚合，直接建構 schemas 的 pydantic
- schemas：HTTP 回應 pydantic（API enum 契約）
"""

from app.services.radar.compute import (  # noqa: F401
    build_broker_history,
    build_instrument_slim,
    build_overview,
)
from app.services.radar.queries import (  # noqa: F401
    fetch_broker_signals,
    fetch_coverage_counts,
    fetch_instrument_signals,
    fetch_signals_for_instruments,
    list_radar_instruments,
)
