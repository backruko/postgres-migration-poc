# 1. テーブルもデータも全て破壊して、完全に初期状態に戻す
# alembic downgrade base

# 2. 最新の設計図（テーブル）をもう一度作り直す（データは空っぽ）
# alembic upgrade head

import string
from faker import Faker
# ※インポートパスはご自身の環境に合わせて修正してください
from src.infrastructure.database import SessionLocal
from src.domain_client.models import ClientMaster

# 言語を日本語（日本用の会社名や名前）に設定
fake = Faker('ja_JP')

def run_seed():
    db = SessionLocal()
    try:
        print("🧹 既存のデータを削除しています...")
        # 1. まずテーブルの中身を空にする
        db.query(ClientMaster).delete()
        
        print("🌱 ダミーデータを生成しています...")
        fake_clients = []
        
        # 2. Fakerを使って50件のダミーデータを作る
        for _ in range(50):
            client = ClientMaster(
                # idはPK（自動採番）のためここでは指定しません（DBが勝手に「1, 2, 3...」と振ってくれます）
                
                # code: 4つのランダムなアルファベット大文字（例: "ABCD", "XQWZ"）
                # '????' の部分が letters で指定した文字からランダムに選ばれます
                code=fake.lexify(text='????', letters=string.ascii_uppercase),
                
                # name: 架空の会社名（例: "株式会社鈴木", "佐藤商事" など）
                name=fake.company()
            )
            fake_clients.append(client)
        
        # 3. 50件のデータをDBに一括登録
        db.add_all(fake_clients)
        db.commit()
        
        print("✅ 50件のダミーデータの録入が完了しました！")
        
    except Exception as e:
        db.rollback()
        print(f"❌ エラーが発生しました: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    run_seed()