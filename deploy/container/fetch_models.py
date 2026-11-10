"""映像建置期：把嵌入與 rerank 權重下載進 HF 快取，再以離線模式載入一次證明執行期不需要網路。

只在 `deploy/container/Dockerfile` 的 build 階段跑（`HF_HOME` 指到映像內的唯讀目錄）。執行期一律
`HF_HUB_OFFLINE=1`：權重缺了就是映像建錯，要在 build 時失敗，而不是讓第一個查詢去外網下載。

用法：
    python deploy/container/fetch_models.py download   # 需要網路（build 階段）
    python deploy/container/fetch_models.py verify     # 必須在 HF_HUB_OFFLINE=1 下跑，真的載入模型

模型名不在這裡另寫一份：嵌入取 `app.services.embed.MODEL_NAME`（BGE-M3），rerank 取 `RERANK_MODEL`
（預設與 `app/config.py` 相同，build arg 可改；改了執行期也要設同一個值）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# BGE-M3 的 repo 另有一份 ONNX 匯出（約 2.2 GB）與說明圖片，FlagEmbedding 用不到。執行期的
# snapshot_download（FlagEmbedding 載入時會呼叫）在離線模式下只認快取裡的 snapshot，不檢查這些檔在不在。
_EMBED_IGNORE = ["flax_model.msgpack", "rust_model.ot", "tf_model.h5", "onnx/*", "imgs/*", "*.onnx", "*.onnx_data"]
_RERANK_IGNORE = ["flax_model.msgpack", "rust_model.ot", "tf_model.h5", "onnx/*", "*.onnx", "*.onnx_data"]


def _models() -> list[tuple[str, list[str]]]:
    from app.services.embed import MODEL_NAME

    rerank = (os.environ.get("RERANK_MODEL") or "").strip() or "BAAI/bge-reranker-v2-m3"
    return [(MODEL_NAME, _EMBED_IGNORE), (rerank, _RERANK_IGNORE)]


def download() -> int:
    from huggingface_hub import snapshot_download

    if os.environ.get("HF_HUB_OFFLINE"):
        print("download 需要網路：請在 build 階段、未設 HF_HUB_OFFLINE 時執行", file=sys.stderr)
        return 2
    for repo_id, ignore in _models():
        path = snapshot_download(repo_id=repo_id, ignore_patterns=ignore)
        print(f"已下載 {repo_id} → {path}")
    return 0


def verify() -> int:
    if os.environ.get("HF_HUB_OFFLINE") != "1":
        print("verify 必須在 HF_HUB_OFFLINE=1 下執行（證明執行期不需要網路）", file=sys.stderr)
        return 2
    from app.services import embed, rerank

    vec = embed.embed_query("warmup")
    if len(vec) != embed.EMBED_DIM:
        print(f"嵌入維度 {len(vec)}，預期 {embed.EMBED_DIM}", file=sys.stderr)
        return 1
    model = rerank._get_model()
    if model is None:
        print("rerank 模型離線載入失敗（見上方 log）", file=sys.stderr)
        return 1
    print("離線載入成功：嵌入與 rerank 模型都在映像裡")
    return 0


def main(argv: list[str]) -> int:
    if argv[1:] == ["download"]:
        return download()
    if argv[1:] == ["verify"]:
        return verify()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
