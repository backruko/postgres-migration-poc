"""
職員を有効（active）な状態で直接登録します。最初のシステム管理者を作るためのスクリプトです
（それ以降の職員は、システム管理者が API / 画面から登録します）。

    python scripts/create_staff.py <login_id> <氏名> --roles system_admin [--email someone@example.com]

接続先は .env の DATABASE_URL（環境変数 DATABASE_URL が優先）。登録した職員の ID を表示します
（ログインが入るまでは、この ID を X-Staff-Id ヘッダーで指定して操作します）。
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core.exceptions import BusinessException  # noqa: E402
from src.domain_staff.models import RoleCode, StaffStatus  # noqa: E402
from src.domain_staff.schemas import StaffCreate  # noqa: E402
from src.domain_staff.services import create_staff  # noqa: E402
from src.infrastructure.database import SessionLocal, engine  # noqa: E402

engine.echo = False


def main() -> int:
    parser = argparse.ArgumentParser(description="Register an active staff user (e.g. the first system admin).")
    parser.add_argument("login_id")
    parser.add_argument("name")
    parser.add_argument("--email", default=None)
    parser.add_argument("--roles", nargs="+", default=[RoleCode.SYSTEM_ADMIN.value], choices=[r.value for r in RoleCode])
    args = parser.parse_args()

    db = SessionLocal()
    try:
        staff = create_staff(
            db,
            StaffCreate(login_id=args.login_id, name=args.name, email=args.email, roles=[RoleCode(r) for r in args.roles]),
            actor=None,
            status=StaffStatus.ACTIVE,
        )
        # セッションを閉じる前に（ロールは遅延読み込みのため）
        print(f"Registered staff user id={staff.id} login_id={staff.login_id} roles={[r.value for r in staff.role_codes]}")
    except BusinessException as e:
        print(f"Could not register the staff user: {e.message}", file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
