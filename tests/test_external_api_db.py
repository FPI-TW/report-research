"""對外 API 的資料邊界對真的 PostgreSQL 成立（`web/routers/external.py` ＋ `app/services/entitlement.py`）。

`tests/test_external_api.py` 用假 session 依綁定參數模擬 SQL；這裡驗 SQL 本身：

- file-url 用的那一條查詢（`external.fetch_file_pointer`）與搜尋附連結用的批次查詢
  （`external.fetch_file_pointers`）：被隱藏的研報、草稿、授權範圍外的研報都取不到，授權範圍內可取得。
- `hybrid_search` 的 dense 與字面兩路帶 entitlement 時，授權範圍外的研報不會出現（store 兩個函式各驗一次，
  再驗合起來的 `hybrid_search`）。

跑在 CI 的「schema 契約」job；本機沒有 DB（或庫還沒套 revision 0008）就 skip，`REPORT_MARK_REQUIRE_DB=1`
時改成失敗。**一律 rollback、絕不 commit**——本機預設連到的是測試環境的真實資料庫。`upsert_report` 本身會 commit，
測試把那個 session 的 commit 換成 flush。斷言只針對自己塞進去的列（以 file_hash／report_id 辨識），
不假設庫是空的：向量用與查詢完全相同的方向（距離 ≈ 0）、字面用語料裡不存在的詞。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import unittest
import uuid
from datetime import date

from sqlalchemy import text

from app.services import store
from app.services.entitlement import Entitlement
from app.services.retrieval import hybrid_search
from web.routers import external

DIM = 1024
TERM = "zqextapiprobe"  # 語料裡不存在的字面詞
REPORT_DATE = date(2099, 1, 2)


def _skip_or_raise(exc: Exception, why: str) -> None:
    if os.getenv("REPORT_MARK_REQUIRE_DB"):
        raise exc
    raise unittest.SkipTest(f"{why}：{exc}")


async def _with_session(fn):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.services.db import DATABASE_URL

    eng = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
    try:
        async with AsyncSession(eng, expire_on_commit=False) as session:
            try:
                return await fn(session)
            finally:
                await session.rollback()
    finally:
        await eng.dispose()


def _vec(seed: int) -> list[float]:
    v = [0.0] * DIM
    v[0] = 1.0
    v[1 + seed] = 0.001
    return v


def _query_vec() -> list[float]:
    v = [0.0] * DIM
    v[0] = 1.0
    return v


class _NoCommit:
    """把 session.commit 暫時換成 flush：upsert_report 會 commit，測試必須能整個 rollback。"""

    def __init__(self, session):
        self.session = session

    def __enter__(self):
        self._orig = self.session.commit
        self.session.commit = self.session.flush
        return self

    def __exit__(self, *exc):
        self.session.commit = self._orig
        return False


def _hash(name: str) -> str:
    return hashlib.sha256(f"extapi-{name}-{uuid.uuid4().hex}".encode()).hexdigest()


async def _ingest(session, name: str, file_hash: str, *, market: str, source: str = "kgi") -> str:
    row = store.ReportRow(
        file_hash=file_hash, file_name=f"{name}.pdf", file_path=f"/nonexistent/{name}.pdf",
        source_object_key=f"originals/{file_hash[:2]}/{file_hash}.pdf",
        market=market, is_research=True, confidence=1.0, source=source, report_date=REPORT_DATE,
        report_type="個股", instrument_types=["stock"], relates_stock=True, full_text=f"{TERM} 全文 {name}",
    )
    chunks = [f"{TERM} 對外API測試 {name} 第{i}段" for i in range(2)]
    with _NoCommit(session):
        return await store.upsert_report(session, row, chunks, [_vec(i) for i in range(2)])


async def _set_visibility(session, file_hash: str, *, hidden: bool, publication: str) -> None:
    await session.execute(
        text(
            "INSERT INTO research.report_visibility (file_hash, hidden, reason, publication) "
            "VALUES (:h, :hidden, :reason, :pub)"
        ),
        {"h": file_hash, "hidden": hidden, "reason": "測試：對外 API" if hidden else None, "pub": publication},
    )


async def _setup(session) -> dict[str, tuple[str, str]]:
    """{名稱: (report_id, file_hash)}：TW 可見、TW 隱藏、TW 草稿、US 可見。"""
    out = {}
    for name, market in (("tw", "TW"), ("hidden", "TW"), ("draft", "TW"), ("us", "US")):
        h = _hash(name)
        out[name] = (await _ingest(session, name, h, market=market), h)
    await _set_visibility(session, out["hidden"][1], hidden=True, publication="published")
    await _set_visibility(session, out["draft"][1], hidden=False, publication="draft")
    return out


TW = Entitlement(markets=("TW",))
US = Entitlement(markets=("US",))


class ExternalApiDbTests(unittest.TestCase):
    def _run(self, fn):
        try:
            return asyncio.run(_with_session(fn))
        except (unittest.SkipTest, AssertionError):
            raise
        except Exception as exc:
            msg = repr(exc)
            if "UndefinedTable" in msg or "UndefinedColumn" in msg or "does not exist" in msg:
                _skip_or_raise(exc, "庫尚未套用 revision 0008")
            _skip_or_raise(exc, f"DB 不可用（{type(exc).__name__}）")

    def test_file_url_query_excludes_hidden_draft_and_out_of_entitlement(self):
        async def fn(session):
            rows = await _setup(session)
            single = {}
            for ent_name, ent in (
                ("TW", TW), ("US", US), ("TW+US", Entitlement(markets=("TW", "US"))),
                ("TW/ms", Entitlement(markets=("TW",), sources=("ms",))),
                ("TW/futures", Entitlement(markets=("TW",), instrument_types=("futures",))),
            ):
                single[ent_name] = {
                    name for name, (rid, _h) in rows.items()
                    if await external.fetch_file_pointer(session, rid, ent) is not None
                }
            ids = [rid for rid, _h in rows.values()]
            batch = await external.fetch_file_pointers(session, ids, Entitlement(markets=("TW", "US")))
            by_id = {rid: name for name, (rid, _h) in rows.items()}
            tw_rid, tw_hash = rows["tw"]
            pointer = await external.fetch_file_pointer(session, tw_rid, TW)
            return single, {by_id[r] for r in batch}, pointer, tw_hash, batch.get(tw_rid)

        single, batch, pointer, tw_hash, batch_tw = self._run(fn)
        self.assertEqual(single["TW"], {"tw"})  # 隱藏與草稿都取不到；US 不在授權內
        self.assertEqual(single["US"], {"us"})
        self.assertEqual(single["TW+US"], {"tw", "us"})
        self.assertEqual(single["TW/ms"], set())  # source 維度授權外
        self.assertEqual(single["TW/futures"], set())  # instrument_type 維度授權外
        self.assertEqual(batch, {"tw", "us"})
        self.assertEqual(tuple(pointer), ("tw.pdf", tw_hash, f"originals/{tw_hash[:2]}/{tw_hash}.pdf"))
        self.assertEqual(batch_tw, ("tw.pdf", tw_hash, f"originals/{tw_hash[:2]}/{tw_hash}.pdf"))

    def test_hybrid_search_dense_and_lexical_respect_entitlement(self):
        async def fn(session):
            rows = await _setup(session)
            names = {h: name for name, (_rid, h) in rows.items()}
            out = {}
            for label, ent in (("none", None), ("TW", TW), ("US", US)):
                dense = await store.search_chunks_meta(session, _query_vec(), scan=400, entitlement=ent)
                lex, _ = await store.search_chunks_lexical(
                    session, _query_vec(), [f"%{TERM}%"], cap=2000, entitlement=ent,
                )
                scored = await hybrid_search(session, TERM, _query_vec(), k=10, dense_scan=400, entitlement=ent)
                out[label] = {
                    "dense": {names[r.file_hash] for r in dense if r.file_hash in names},
                    "lexical": {names[r.file_hash] for r in lex if r.file_hash in names},
                    "hybrid": {names[r.file_hash] for _t, _s, r in scored if r.file_hash in names},
                }
            return out

        out = self._run(fn)
        for path in ("dense", "lexical", "hybrid"):
            with self.subTest(path=path):
                # 沒有 entitlement（站內）：可見的兩篇都在，隱藏與草稿本來就不可見
                self.assertEqual(out["none"][path], {"tw", "us"})
                self.assertEqual(out["TW"][path], {"tw"})
                self.assertEqual(out["US"][path], {"us"})


if __name__ == "__main__":
    unittest.main()
