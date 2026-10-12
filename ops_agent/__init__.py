"""report-mark-ops-agent：單機維運代理（status、logs；白名單 restart、run）。

Web 不直接碰 systemctl／journalctl／docker：它只透過 Unix socket 對這支代理送**固定白名單**的
請求（一行一個 JSON），代理只對 Service Catalog（`deploy/ops/services.*.toml`）列出的服務、
catalog 允許的 action 動作。不接受任意 shell、任意 unit、stop、timer enable/disable。
寫入類只有 v1 拍板的兩種：Web 的 restart、既有 oneshot 的 run（`systemctl start --no-block`，不收參數）；
PostgreSQL、nginx、cloudflared 永遠唯讀（`ops_agent/actions.py` 硬擋，catalog 寫了也放不開）。

**刻意只用標準庫、不 import `app.*`、不依賴 `.venv`**——理由同 `scripts/collect_resource_usage.py`
與 P4 探針：2026-08-18 的中斷根因是 venv 損毀，相依 Python 環境的維運工具會跟被監控的東西一起死，
而那正是最需要它的時候。系統的 `/usr/bin/python3`（3.11+，要 `tomllib`）就跑得動。

部署時程式碼複製到 root 擁有的 `/opt/report-mark-ops/`（不是從 repo 執行）：代理的使用者在
docker 群組（等同 root），程式碼若可被 web 的使用者改寫，web 被攻破就等於 root。安裝步驟見
`docs/production_resilience.md`「維運代理」。

模組：
- `protocol.py`：協定常數、請求驗證、`since`／`lines` 參數邊界。
- `catalog.py`：Service Catalog 載入與驗證（含 dev／prod 交叉拒絕）。
- `runner.py`：子行程執行（逾時、輸出上限、最小環境）。測試換成假的 runner。
- `backends.py`：systemctl show／journalctl／docker inspect／docker logs 的參數組裝與輸出解析。
- `actions.py`：寫入類（restart、run）的硬性限制、execution group 互斥、鎖檔試探與 argv。
- `server.py`：asyncio Unix socket server（SO_PEERCRED、逾時、回應上限）與 CLI。
"""
