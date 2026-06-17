# web/env_loader.py
"""安全載入 repo 根 .env。

只做 dotenv 常見語法的最小子集：
- 忽略空白行與 `# ...` 註解
- 支援 `KEY=value`
- 若 value 以成對單/雙引號包住，僅去掉最外層引號

刻意不做 shell 展開、命令替換或跳脫序列求值，避免把 secrets 當 shell script 執行。
"""

from __future__ import annotations

import os
from pathlib import Path


def _parse_line(line: str) -> tuple[str, str] | None:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None
    if raw.startswith("export "):
        raw = raw[7:].lstrip()
    if "=" not in raw:
        return None
    key, value = raw.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    if len(value) >= 2 and value[:1] == value[-1:] and value[:1] in {'"', "'"}:
        value = value[1:-1]
    return key, value


def load_env_file(path: str | Path, *, override: bool = False) -> None:
    env_path = Path(path)
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_line(line)
        if parsed is None:
            continue
        key, value = parsed
        if override or key not in os.environ:
            os.environ[key] = value
