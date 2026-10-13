"""發布本機的主機信標到 R2（``beacons/<HOST_BEACON_NAME>.json``），給另一台主機的後台監看。

信標內容與兩端的分工見 ``app/services/host_beacon.py``。這支只做三件事：讀 sync 殼留在 ``data/`` 的狀態檔、
問本機的維運代理要服務清單（``list``）、把結果覆寫到 R2。由 ``report-mark-beacon.timer`` 每 10 分鐘跑一次。

Examples:
    HOST_BEACON_NAME=office uv run python scripts/publish_host_beacon.py
    uv run python scripts/publish_host_beacon.py --dry-run     # 只印出信標，不碰 R2

零 LLM、不碰 DB、不載模型。環境由 unit 的 ``EnvironmentFile=/etc/default/report-mark-sync`` 提供（R2 憑證、
``SYNC_SOURCE``、``SYNC_INBOX_PUSH``、``HOST_BEACON_NAME`` 都在那裡）；**刻意不讀 repo 根的 ``.env``**，與其他批次相同。

維運代理問不到（沒裝、沒啟動、逾時）不擋發布：信標照發，``services`` 是 null、``services_error`` 寫原因——
來源段的判斷靠的是 ``data/`` 的狀態檔，不靠代理。

退出碼：0＝已發布，或這台不發布（``HOST_BEACON_NAME`` 沒設）；1＝發布失敗（R2 寫不進去）；2＝設定不足以發布
（``OBJECT_STORAGE_MODE=local``）。1 與 2 都不走告警鏈：這支失敗的後果就是信標變舊，另一台的探針會以 ``stale``
告警——那才是有人在看的地方；這裡再告警一次只是同一件事響兩遍。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.services import host_beacon  # noqa: E402
from app.services.object_storage import ObjectStorageError, get_object_storage  # noqa: E402
from web import ops_client  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
# 代理的 list 是一次 systemctl show＋一次 docker inspect，通常不到一秒；主機吃緊時 docker inspect 可以慢到十幾秒
# （代理自己的單一指令上限是 8 秒）。上限與 web 的預設相同，等不到就照樣發布（services 為 null）。
AGENT_TIMEOUT = 20.0


async def _list_services() -> tuple[list[dict] | None, str | None]:
    client = ops_client.default_client()
    client.timeout = min(client.timeout, AGENT_TIMEOUT)
    try:
        listing = await client.request("list")
    except (ops_client.OpsAgentUnavailable, ops_client.OpsAgentError) as exc:
        return None, f"維運代理問不到：{exc}"
    return host_beacon.project_services(listing), None


def collect(name: str, data_dir: Path) -> dict:
    services, error = asyncio.run(_list_services())
    return host_beacon.build(
        name=name, host=socket.gethostname(), published_epoch=int(time.time()),
        sync=host_beacon.collect_sync(data_dir, os.environ), services=services, services_error=error,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="發布本機的主機信標到 R2（beacons/<名稱>.json）")
    parser.add_argument("--dry-run", action="store_true", help="只把信標印到 stdout，不寫 R2")
    args = parser.parse_args()

    name = get_settings().host_beacon_name
    if not name:
        print("[beacon] HOST_BEACON_NAME 沒設（或不合法）：這台不發布信標", file=sys.stderr)
        return 0
    beacon = collect(name, DATA_DIR)
    if args.dry_run:
        print(json.dumps(beacon, ensure_ascii=False, indent=2))
        return 0
    storage = get_object_storage()
    if not storage.enabled:
        print("[beacon] OBJECT_STORAGE_MODE=local：信標需要 R2，結束", file=sys.stderr)
        return 2
    try:
        storage.put_status_object(host_beacon.object_key(name), host_beacon.encode(beacon))
    except (ObjectStorageError, host_beacon.BeaconError) as exc:
        print(f"[beacon] 發布失敗：{exc}", file=sys.stderr)
        return 1
    sync = beacon["sync"]
    feed = sync["feed"] or {}
    print(f"[beacon] 已發布 {host_beacon.object_key(name)}：round={sync['round']['state']} "
          f"feed={feed.get('result', '（無）')} push={feed.get('push', '（無）')} "
          f"pending_replays={sync['pending_replays']} services={len(beacon['services'] or [])}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
