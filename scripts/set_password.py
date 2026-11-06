"""
既存の職員のパスワードを設定します（LG.2）。ログインを入れたあと、最初に自分（システム管理者）のパスワードを決めるときや、
画面に入れなくなったときに使います（ふだんは 職員 の画面の「仮パスワードを設定」）。

    python scripts/set_password.py <login_id> [--temporary]

パスワードを 2 回たずねます（画面に出ません）。--temporary なら仮パスワード（次のログインで本人が変える）。
ロックは解け、その職員の開いているセッションは終わります。接続先は .env の DATABASE_URL（環境変数 DATABASE_URL が優先）。
"""
import argparse
import getpass
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core.exceptions import BusinessException  # noqa: E402
from src.domain_staff import credentials  # noqa: E402
from src.infrastructure.database import SessionLocal, engine  # noqa: E402
from src.infrastructure.repositories import staff_user_repo  # noqa: E402

engine.echo = False


def main() -> int:
    parser = argparse.ArgumentParser(description="Set the password of an existing staff user.")
    parser.add_argument("login_id")
    parser.add_argument("--temporary", action="store_true", help="the person must change it at the next login")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        staff = staff_user_repo.get_by_login_id(db, login_id=args.login_id)
        if staff is None:
            print(f"No staff user with login id '{args.login_id}'.", file=sys.stderr)
            return 1
        password = getpass.getpass(f"New password for {staff.login_id} ({staff.name}): ")
        if getpass.getpass("Again: ") != password:
            print("The two passwords differ.", file=sys.stderr)
            return 1
        credentials.set_password(db, staff, password, temporary=args.temporary, now=datetime.now(timezone.utc))
        db.commit()
        kind = "temporary password" if args.temporary else "password"
        print(f"Set the {kind} of staff user id={staff.id} login_id={staff.login_id} (status {staff.status.value}).")
    except BusinessException as e:
        db.rollback()
        print(f"Could not set the password: {e.message}", file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
