"""語意檢索驗證工具。

用法：
  uv run python scripts/search.py "AI 伺服器需求展望"
  uv run python scripts/search.py "利率展望" --market 債券 --k 5
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.db import SessionFactory  # noqa: E402
from app.services.embed import embed_query  # noqa: E402
from app.services.store import search_chunks  # noqa: E402


async def main(query: str, k: int, market: str | None) -> None:
    qvec = embed_query(query)
    async with SessionFactory() as session:
        rows = await search_chunks(session, qvec, top_k=k, market=market)

    print(f"\nquery: {query!r}" + (f"  [market={market}]" if market else ""))
    print("=" * 70)
    for i, (file_name, mkt, content, distance) in enumerate(rows, 1):
        snippet = content.replace("\n", " ")[:140]
        print(f"{i}. [{mkt}] {file_name[:50]}  (dist={distance:.4f})")
        print(f"   {snippet}…\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--market", default=None)
    args = ap.parse_args()
    asyncio.run(main(args.query, args.k, args.market))
