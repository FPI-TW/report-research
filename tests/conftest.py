"""
pytest 測試環境初始化。

1. 將 repo root 加入 sys.path（pytest 預設不加），讓 `web`、`app` 等頂層套件可被匯入。
2. 為 web.auth fail-closed env var 設定測試預設值（individual 測試可在匯入前覆蓋）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Repo root = parent of this tests/ directory
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# web.auth is fail-closed: raise RuntimeError if these are unset at import time.
# These defaults are applied before any test module is imported, so per-module
# setdefault() calls (e.g. in test_auth.py) intentionally defer to these values.
os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")
