"""資料來源連接器：把券商自己的研報來源接上共用的入庫管線。

連接器**只**負責「把新檔落地到本機資料夾、附上 metadata 側檔」；之後的掃描、去重、抽字、標註、切塊、
嵌入、入庫全部共用（`base.scan` → `scripts/_ingest_core.ingest_one`，入口是 `scripts/ingest_connectors.py`）。
以 file_hash 去重：同一份檔案從兩個連接器進來，第二次是 `skip_exists`。

設定在 profile 的 `[connectors]`（`app/profile.py` 的 `Connectors`／`ConnectorSpec`）：`enabled` 是要輪詢的
連接器，`[connectors.sources.<name>]` 是各自的非祕密參數。祕密走環境變數。

## 類型

- `folder`：監看本機資料夾。券商自己的 SMB／NFS 由主機（或 compose volume）掛上，框架不做 mount。
  `fetch()` 什麼都不做，檔案本來就在 `path`。
- `s3`：S3 相容 bucket 的某個 prefix（`s3.S3Connector`），拉到 `landing`。主機層 sync 殼的 R2 inbox 是它的一個實例
  （`scripts/r2_inbox.py pull` 與它共用 `s3.pull`）。
- `plugin`：profile 的 `plugins/<名稱>.py`。**只載入 profile 以名稱明確引用的那一個檔**（`[connectors.sources.x]`
  的 `plugin = "<名稱>"`，`app/profile.py` 驗證檔案存在），不掃描目錄、不自動 import 其他檔。plugin 模組要
  定義 `fetch(landing: Path, params: Mapping[str, Any]) -> FetchReport`：把新檔（與同名 JSON 側檔）寫進
  `landing`，回報新落地幾檔、失敗幾檔。載入失敗（語法錯、import 錯、缺 `fetch`）拋 `ConnectorError`，
  該連接器這一輪不跑並計成異常，不靜默略過。

對外上傳 API（`POST /external/v1/uploads`）不是輪詢型連接器：檔案進上傳流程（隔離、預檢、ClamAV、人工
發布），見 `web/routers/external.py`。

## 落地資料夾要保留

入庫後 `research_report.file_path` 指向落地的檔案（`OBJECT_STORAGE_MODE=local` 時 web 從這裡交原檔），
所以 `path`／`landing` 必須是長期保留的位置，容器部署時 web 與 scheduler 都要掛得到（同一個路徑）。
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Protocol

from app.profile import REPO_ROOT, ConnectorSpec
from app.services.connectors.base import FetchReport

DATA_DIR_ENV = "CONNECTOR_DATA_DIR"


class ConnectorError(RuntimeError):
    """連接器建不起來或 plugin 載入失敗（整個連接器這一輪不跑）。"""


class Connector(Protocol):
    name: str
    kind: str
    root: Path

    def fetch(self) -> FetchReport: ...


def data_dir() -> Path:
    """狀態檔、失敗清單、本輪 hashes 的目錄：`CONNECTOR_DATA_DIR`（app/config.py）；空＝repo 根 data/connectors/。"""
    from app.config import get_settings

    raw = get_settings().connector_data_dir
    path = Path(raw) if raw else REPO_ROOT / "data" / "connectors"
    return path if path.is_absolute() else REPO_ROOT / path


def state_path(name: str) -> Path:
    return data_dir() / "state" / f"{name}.json"


def due_specs(specs, *, force: bool = False, now: float | None = None):
    """（到期的 spec, 狀態讀不到的 [(spec, 錯誤)]）。狀態檔壞掉不當成「到期」：先要人修，不整批重抽。"""
    from app.services.connectors.base import ConnectorState

    due, broken = [], []
    for spec in specs:
        try:
            state = ConnectorState.load(state_path(spec.name))
        except ValueError as exc:
            broken.append((spec, str(exc)))
            continue
        if force or state.due(spec.poll_minutes, now=now):
            due.append(spec)
    return due, broken


class FolderConnector:
    kind = "folder"

    def __init__(self, name: str, root: Path) -> None:
        self.name = name
        self.root = root

    def fetch(self) -> FetchReport:
        if not self.root.is_dir():
            # 掛載掉了或路徑打錯：當成「一篇都沒有」會讓來源斷掉卻沒有任何訊號。
            return FetchReport(failed=1, errors=(f"監看的資料夾不存在或不是資料夾：{self.root}",))
        return FetchReport()


class PluginConnector:
    kind = "plugin"

    def __init__(self, name: str, root: Path, module: ModuleType, params: Mapping[str, Any]) -> None:
        self.name = name
        self.root = root
        self.module = module
        self.params = params

    def fetch(self) -> FetchReport:
        self.root.mkdir(parents=True, exist_ok=True)
        result = self.module.fetch(self.root, self.params)
        if not isinstance(result, FetchReport):
            raise ConnectorError(f"plugin {self.name} 的 fetch() 必須回傳 FetchReport，目前是 {type(result).__name__}")
        return result


def load_plugin(path: Path, name: str) -> ModuleType:
    """以檔案路徑載入一個 plugin 模組（不經 sys.path、不 import 同目錄的其他檔）。"""
    module_name = f"report_mark_connector_plugin_{name.replace('-', '_')}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ConnectorError(f"無法載入 plugin：{path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except ConnectorError:
        raise
    except BaseException as exc:  # noqa: BLE001 — 任何載入期的錯誤都要說出是哪個 plugin
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise ConnectorError(f"plugin 載入失敗：{path}：{type(exc).__name__}: {exc}") from exc
    if not callable(getattr(module, "fetch", None)):
        raise ConnectorError(f"plugin 缺 fetch(landing, params)：{path}")
    return module


def build(spec: ConnectorSpec, *, environ: Mapping[str, str] | None = None, s3_client=None) -> Connector:
    """ConnectorSpec → 連接器。s3 缺環境變數、plugin 載入失敗拋 `ConnectorError`。"""
    if spec.kind == "folder":
        assert spec.path is not None
        return FolderConnector(spec.name, spec.path)
    assert spec.landing is not None
    if spec.kind == "s3":
        from app.services.connectors.s3 import BucketConfig, ConnectorConfigError, S3Bucket, S3Connector

        try:
            config = BucketConfig.from_env(spec.env or "", os.environ if environ is None else environ)
        except ConnectorConfigError as exc:
            raise ConnectorError(f"{spec.name}：{exc}") from exc
        return S3Connector(spec.name, spec.prefix or "", spec.landing, S3Bucket(config, client=s3_client))
    if spec.kind == "plugin":
        assert spec.plugin is not None
        return PluginConnector(spec.name, spec.landing, load_plugin(spec.plugin, spec.name), spec.params)
    raise ConnectorError(f"不支援的連接器類型：{spec.kind}")


__all__ = [
    "Connector", "ConnectorError", "DATA_DIR_ENV", "FetchReport", "FolderConnector", "PluginConnector", "build",
    "data_dir", "due_specs", "load_plugin", "state_path",
]
