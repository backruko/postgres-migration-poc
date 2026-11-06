"""
マスタ（顧客・業務）を止める前の確認（M1.1）。client / biz はほかのドメインを知らないため、ここで確認を登録します。

- 顧客の無効化: 有効な（論理削除されていない）業務が残っている間は断る
- 業務の削除: 有効なテンプレート、または完了・取消以外のオーダーが残っている間は断る
  （依存関係の定義は biz 自身が確かめる）

register() は order_sync.register() から呼ばれる（main.py・tests/conftest.py・スクリプトで登録済み）。
"""
from sqlalchemy.orm import Session

from src.domain_biz import delete_checks
from src.domain_biz.models import BizMaster
from src.domain_client import deactivation_checks
from src.domain_client.models import ClientMaster
from src.infrastructure.repositories import biz_master_repo, ope_order_repo, plan_template_repo


def register() -> None:
    deactivation_checks.register_deactivation_check(client_businesses_alive)
    delete_checks.register_delete_check(business_templates_alive)
    delete_checks.register_delete_check(business_orders_unfinished)


def client_businesses_alive(db: Session, client: ClientMaster) -> list[str]:
    count = biz_master_repo.count_alive_of_client(db, client_code=client.code)
    return [f"{count} active business(es); delete them first"] if count else []


def business_templates_alive(db: Session, biz: BizMaster) -> list[str]:
    count = len(plan_template_repo.get_by_biz(db, biz_master_id=biz.id))
    return [f"{count} plan template(s)"] if count else []


def business_orders_unfinished(db: Session, biz: BizMaster) -> list[str]:
    count = ope_order_repo.count_unfinished_of_biz(db, biz_id=biz.id)
    return [f"{count} order(s) not finished or canceled"] if count else []
