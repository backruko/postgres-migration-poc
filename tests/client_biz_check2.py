import pytest
from sqlalchemy.exc import IntegrityError
from src.domain_client.models import ClientMaster
from src.domain_biz.models import BizMaster

# ==========================================
# 1. 外部キー制約のエラーテスト（親が存在しない場合）
# ==========================================
def test_create_biz_master_fk_error(db_session):
    # 存在しない client_code "FAKE" を指定して子テーブルを作成
    biz = BizMaster(
        client_code="FAKE",
        business_seq=1,
        name="テスト処理",
        description="親がいないデータ"
    )
    db_session.add(biz)
    
    # 期待される結果: 親テーブル(client_master)に "FAKE" が存在しないため、
    # DB側で外部キー制約違反となり、IntegrityError が発生する
    with pytest.raises(IntegrityError):
        db_session.flush() # commit から flush に変更


# ==========================================
# 2. ユニーク制約（重複）のエラーテスト
# ==========================================
def test_create_biz_master_unique_error(db_session):
    # ステップ1: 親データと、1つ目の正常な子データを作成
    client = ClientMaster(code="TEST", name="テスト株式会社")
    db_session.add(client)
    db_session.flush()

    biz1 = BizMaster(client_code="TEST", business_seq=1, name="処理1")
    db_session.add(biz1)
    db_session.flush() # commit から flush に変更（正常に登録）

    # ステップ2: 全く同じ client_code と business_seq で2つ目のデータを作成
    # （または business_code が "TEST0001" になるようなデータ）
    biz2 = BizMaster(client_code="TEST", business_seq=1, name="処理2")
    db_session.add(biz2)
    
    # 期待される結果: business_code が重複するか、
    # 複合ユニーク制約（client_code, business_seq）に違反するため、IntegrityError が発生する
    with pytest.raises(IntegrityError):
        db_session.flush() # commit から flush に変更


# ==========================================
# 3. 境界値（桁数オーバー）のテスト
# ==========================================
def test_create_biz_master_boundary_values(db_session):
    # 親データを作成
    client = ClientMaster(code="TEST", name="テスト株式会社")
    db_session.add(client)
    db_session.flush()

    # --- 境界値1: 4桁の最大値 (9999) ---
    biz_9999 = BizMaster(client_code="TEST", business_seq=9999, name="ギリギリの処理")
    db_session.add(biz_9999)
    db_session.flush() # commit から flush に変更
    db_session.refresh(biz_9999)
    
    # 想定通り "TEST9999" になることを確認
    assert biz_9999.business_code == "TEST9999"

    # --- 境界値2: 5桁に突入した場合 (10000) ---
    biz_10000 = BizMaster(client_code="TEST", business_seq=10000, name="桁あふれ処理")
    db_session.add(biz_10000)
    db_session.flush() # commit から flush に変更
    db_session.refresh(biz_10000)
    
    # 【注意】PostgreSQLの LPAD('10000', 4, '0') の仕様では、
    # 指定した長さを超える場合、右側が切り捨てられて '1000' になります。
    # 結果として "TEST" + "1000" = "TEST1000" となってしまい、
    # 既存の sequence=1000 のデータと衝突するリスクがあります。
    # ※ここは要件（切り捨てるのか、そのまま5桁にするのか）に合わせてアサーションを調整してください。
    assert biz_10000.business_code == "TEST1000"