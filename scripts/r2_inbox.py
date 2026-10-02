"""R2 inbox：讓碰不到 NAS 的部署（EC2 staging）也收得到新研報。

辦公室主機的 sync 每輪 rsync NAS → 本地鏡像後，把這一輪的 delta 檔連同相對路徑與 mtime
推到 R2 的 ``inbox/``；另一端的 sync 以 ``SYNC_SOURCE=r2-inbox`` 改從這裡拉，產出與
``rsync --out-format='%n'`` 同格式的 delta，之後的匯入與下游批次一行不改。

Examples:
    uv run python scripts/r2_inbox.py push --delta data/sync_delta_20261002_150000.txt
    uv run python scripts/r2_inbox.py pull --delta data/sync_delta_20261002_180000.txt

為什麼不直接拿 ``originals/``：那裡以 SHA256 定址、metadata 只有 sha256，檔名、相對路徑與
mtime 都不在——而匯入要靠 mtime 補 report_date（``fallback_report_date_from_mtime``）。

「是不是新檔」沿用 rsync ``--size-only`` 的判準：本地鏡像沒有、或大小不同才拉。所以拉的那端
要保留本地鏡像；inbox 本身可以設 R2 生命週期規則定期清掉，已經拉過的不受影響。
零 LLM、不碰 DB。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.object_storage import ObjectStorage, ObjectStorageError, get_object_storage  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
# 與 scripts/sync_new_reports.sh 的 DST 同一個本地鏡像。
LOCAL_MIRROR = ROOT / "研報自動匯入"
INBOX_PREFIX = "inbox/"


def delta_names(lines) -> list[str]:
    """rsync delta → 相對路徑；略過空行與目錄列（'/' 結尾），與 parse_rsync_delta 同一套判準。"""
    out: list[str] = []
    seen: set[str] = set()
    for raw in lines:
        name = raw.strip()
        if not name or name.endswith("/") or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def safe_relative(name: str) -> str | None:
    """inbox 的 key 來自 bucket，不是可信輸入：絕對路徑、``..`` 一律拒收，免得寫出鏡像之外。"""
    p = PurePosixPath(name)
    if not name or p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
        return None
    return str(p)


def push(storage: ObjectStorage, names: list[str], mirror: Path) -> tuple[int, int]:
    pushed = failed = 0
    for name in names:
        rel = safe_relative(name)
        if rel is None or not (mirror / rel).is_file():
            print(f"[push] 略過（不在本地鏡像）：{name}", file=sys.stderr)
            continue
        path = mirror / rel
        try:
            storage.put_transport_file(
                path, INBOX_PREFIX + rel, metadata={"mtime": str(int(path.stat().st_mtime))}
            )
            pushed += 1
        except ObjectStorageError as exc:
            failed += 1
            print(f"[push] 失敗：{name}：{exc}", file=sys.stderr)
    return pushed, failed


def pull(storage: ObjectStorage, mirror: Path) -> tuple[list[str], int]:
    """拉回本地沒有或大小不同的 inbox 物件；回傳（本輪新落地的相對路徑, 失敗數）。"""
    landed: list[str] = []
    failed = 0
    for key, size in storage.list_objects(INBOX_PREFIX):
        rel = safe_relative(key[len(INBOX_PREFIX):])
        if rel is None:
            print(f"[pull] 拒收不安全的 key：{key}", file=sys.stderr)
            continue
        target = mirror / rel
        if target.is_file() and target.stat().st_size == size:
            continue
        tmp = target.with_name(f".{target.name}.part")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            storage.download_to(key, tmp)
            mtime = storage.head_object(key).get("Metadata", {}).get("mtime")
            if mtime and mtime.isdigit():
                os.utime(tmp, (int(mtime), int(mtime)))
            # 先落地成 .part 再改名：中途被殺不會留下以正式檔名存在的半個檔。
            os.replace(tmp, target)
            landed.append(rel)
        except (ObjectStorageError, OSError) as exc:
            failed += 1
            tmp.unlink(missing_ok=True)
            print(f"[pull] 失敗：{key}：{exc}", file=sys.stderr)
    return landed, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="R2 inbox：push（有 NAS 的一端）／pull（沒有 NAS 的一端）")
    parser.add_argument("action", choices=("push", "pull"))
    parser.add_argument("--delta", required=True, help="push 讀這個 delta；pull 把新落地的相對路徑寫到這裡")
    parser.add_argument("--mirror", default=str(LOCAL_MIRROR), help="本地鏡像目錄（預設 repo 根的 研報自動匯入/）")
    args = parser.parse_args()

    storage = get_object_storage()
    if not storage.enabled:
        print("OBJECT_STORAGE_MODE=local：R2 inbox 需要 R2，結束", file=sys.stderr)
        return 2
    mirror = Path(args.mirror)
    if args.action == "push":
        names = delta_names(Path(args.delta).read_text(encoding="utf-8").splitlines())
        pushed, failed = push(storage, names, mirror)
        print(f"[push] 推送 {pushed} 檔、失敗 {failed}", file=sys.stderr)
        return 1 if failed else 0
    landed, failed = pull(storage, mirror)
    # delta 一律寫出（空也寫）：殼以檔案行數計新檔數，與 rsync 那條路徑相同。
    Path(args.delta).write_text("".join(f"{name}\n" for name in landed), encoding="utf-8")
    print(f"[pull] 新落地 {len(landed)} 檔、失敗 {failed}", file=sys.stderr)
    # 部分失敗仍回 0：已落地的照常匯入，失敗的下一輪大小對不上會自然重拉（與 rsync 不同，不會漏）。
    return 0


if __name__ == "__main__":
    sys.exit(main())
