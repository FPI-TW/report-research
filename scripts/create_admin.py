"""建立／重設帳號的 CLI：個別帳號上線時的第一位管理員，以及網頁進不去時的救援入口。

用法（密碼一律互動輸入兩次，或 `--password-stdin` 從標準輸入讀一行；**絕不經 argv**，
argv 會留在 shell 歷史與 `ps` 輸出裡）：

    uv run python scripts/create_admin.py --username alice              # 建立管理員
    uv run python scripts/create_admin.py --username alice --super      # 建立 super admin
    uv run python scripts/create_admin.py --username linebot --role user --password-stdin < pw
    uv run python scripts/create_admin.py --username alice --reset-password   # 忘記密碼／被停用
    uv run python scripts/create_admin.py --username alice --reset-totp       # 驗證器 App 遺失
    uv run python scripts/create_admin.py --from-env                    # 舊共用帳密 → 第一位管理員
    uv run python scripts/create_admin.py --list

`--from-env` 讀 repo 根 `.env` 的 `REPORT_MARK_ACCESS_USERNAME`／`_PASSWORD`（共用帳密時代的
設定）建成管理員；帳號已存在時什麼都不做（可重跑）。舊密碼不符合新政策（至少 10 字元）時
拒絕，請改用 `--username` 互動設定新密碼。轉完之後那兩個鍵就可以從環境檔移除。

`--reset-password` 會重設密碼、重新啟用、撤銷該帳號所有 session；給了 `--role` 才改角色、給了
`--super` 才升為 super admin（救援：super admin 全被停用或忘記密碼時）。`--reset-totp` 關閉該帳號的
兩步驟驗證（手機遺失、網頁上又沒有其他管理員能替他重設時）；可與 `--reset-password` 一起用。
已刪除或排程刪除中的帳號不能經這裡救回（排程中的請先在管理頁取消刪除）。

super admin（才能授予 scope）：`--super` 明確指定；**庫裡沒有任何啟用中的 super admin 時，建立的
管理員自動成為 super**，`--from-env` 轉入的第一位管理員也是——新環境的第一位管理員一定能授權。
每個動作都寫進 `research.admin_audit_log`（actor 為 NULL、detail.via＝cli）。

**連的是 repo 根 `.env` 的 `REPORT_MARK_DB_URL`**（與 web 同一個庫；未設時是程式預設的本機庫）。
這台機器的主 checkout 就是部署目錄，所以在這裡跑就是在改生產帳號。

退出碼：0 成功（含 --from-env 的「已存在」）、1 輸入或帳號狀態不允許、2 DB 不可用。
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# 必須早於 app.services.db：它在 import 期就讀 REPORT_MARK_DB_URL。只補還不存在的鍵。
from web.env_loader import load_env_file  # noqa: E402

load_env_file(REPO_ROOT / ".env")

from app.services import accounts, db, passwords  # noqa: E402

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_DB = 2


def _read_password(args) -> str:
    if args.password_stdin:
        line = sys.stdin.readline()
        return line.rstrip("\r\n")
    first = getpass.getpass("新密碼：")
    second = getpass.getpass("再輸入一次：")
    if first != second:
        raise accounts.InvalidInputError("兩次輸入的密碼不一致")
    return first


async def _list() -> int:
    users = await accounts.list_users()
    if not users:
        print("（還沒有任何帳號）")
    for u in users:
        state = "啟用" if u.enabled else "停用"
        last = u.last_login_at.isoformat(timespec="minutes") if u.last_login_at else "從未登入"
        print(f"{u.username}\t{u.role}\t{state}\t最後登入 {last}\t有效 session {u.active_sessions}")
    return EXIT_OK


async def _from_env() -> int:
    username = os.environ.get("REPORT_MARK_ACCESS_USERNAME", "")
    password = os.environ.get("REPORT_MARK_ACCESS_PASSWORD", "")
    if not username or not password:
        print("環境裡沒有 REPORT_MARK_ACCESS_USERNAME／_PASSWORD，無從轉入", file=sys.stderr)
        return EXIT_INVALID
    if await accounts.find_user_by_username(username) is not None:
        print(f"帳號「{username}」已存在，略過（--from-env 可重跑）")
        return EXIT_OK
    problem = passwords.password_problem(password)
    if problem:
        print(f"舊共用密碼不符合新政策（{problem}）；請改用 --username {username} 互動設定新密碼",
              file=sys.stderr)
        return EXIT_INVALID
    await accounts.create_user(username, password, "admin", actor_id=None, via="cli:from-env", is_super=True)
    print(f"已把舊共用帳密轉成管理員「{username}」；環境檔裡的兩個 REPORT_MARK_ACCESS_* 鍵可以移除了")
    return EXIT_OK


async def _create_or_reset(args) -> int:
    existing = await accounts.find_user_by_username(args.username)
    if args.reset_totp and not args.reset_password:
        if existing is None:
            print(f"帳號「{args.username}」不存在，無從重設", file=sys.stderr)
            return EXIT_INVALID
        await accounts.disable_totp(existing.id, actor_id=None, via="cli")
        print(f"已關閉「{existing.username}」的兩步驟驗證；下次登入只需要密碼")
        return EXIT_OK
    if existing is not None and not args.reset_password:
        print(f"帳號「{existing.username}」已存在；要重設密碼請加 --reset-password", file=sys.stderr)
        return EXIT_INVALID
    if existing is None and args.reset_password:
        print(f"帳號「{args.username}」不存在，無從重設", file=sys.stderr)
        return EXIT_INVALID
    password = _read_password(args)
    role = args.role or (existing.role if existing is not None else "admin")
    if args.super and role != "admin":
        print("--super 只能用在管理員（--role admin）", file=sys.stderr)
        return EXIT_INVALID
    if existing is None:
        make_super = args.super or (role == "admin" and await accounts.count_enabled_supers() == 0)
        info = await accounts.create_user(args.username, password, role, actor_id=None, via="cli",
                                          is_super=make_super)
        print(f"已建立帳號「{info.username}」（{info.role}{'、super admin' if info.is_super else ''}）")
        return EXIT_OK
    await accounts.reset_password(existing.id, password, actor_id=None, via="cli")
    info = await accounts.update_user(existing.id, role=args.role, enabled=True, actor_id=None, via="cli")
    if args.super:
        info = await accounts.set_privileges(existing.id, is_super=True, actor_id=None, via="cli")
    if args.reset_totp:
        info = await accounts.disable_totp(existing.id, actor_id=None, via="cli")
    print(f"已重設「{info.username}」的密碼（{info.role}{'、super admin' if info.is_super else ''}、啟用），"
          "既有 session 全部登出")
    return EXIT_OK


async def _run(args) -> int:
    try:
        if args.list:
            return await _list()
        if args.from_env:
            return await _from_env()
        return await _create_or_reset(args)
    finally:
        # 在同一個 event loop 裡收掉連線池；留給 GC 的話 asyncio.run 關掉 loop 之後才回收，會噴警告。
        await db.engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="建立或重設研報平台帳號（預設建管理員）")
    parser.add_argument("--username", help="帳號名稱（不分大小寫）")
    parser.add_argument("--role", choices=accounts.ROLES, help="建立時預設 admin；重設時給了才改")
    parser.add_argument("--reset-password", action="store_true", help="帳號已存在時重設密碼並重新啟用")
    parser.add_argument("--super", action="store_true", help="建立／重設為 super admin（才能授予 scope）")
    parser.add_argument("--reset-totp", action="store_true", help="關閉該帳號的兩步驟驗證（驗證器遺失）")
    parser.add_argument("--password-stdin", action="store_true", help="從標準輸入讀一行當密碼（非互動）")
    parser.add_argument("--from-env", action="store_true", help="把舊共用帳密轉成第一位管理員")
    parser.add_argument("--list", action="store_true", help="列出所有帳號")
    args = parser.parse_args(argv)
    if not (args.list or args.from_env or args.username):
        parser.error("需要 --username、--from-env 或 --list 其中之一")
    try:
        return asyncio.run(_run(args))
    except accounts.AccountError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INVALID
    except (KeyboardInterrupt, EOFError):
        print("已取消", file=sys.stderr)
        return EXIT_INVALID
    except Exception as exc:  # DB 連不上、schema 沒套（app_user 不存在）
        print(f"DB 不可用或尚未套 schema（make schema）：{exc!r}", file=sys.stderr)
        return EXIT_DB


if __name__ == "__main__":
    sys.exit(main())
