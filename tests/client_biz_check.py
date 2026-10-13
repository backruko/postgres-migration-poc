import pytest
from src.domain_client.models import ClientMaster
from src.domain_biz.models import BizMaster

def test_create_biz_master(db_session):
    # ステップ1: まず外部キーの親となる ClientMaster を作成・挿入する
    client = ClientMaster(
        code="TEST",
        name="テスト株式会社"
    )
    db_session.add(client)
    # flush()によりDBへSQLを発行（未コミット状態）。これにより子テーブル作成時の外部キー制約を満たす
    db_session.flush() 

    # ステップ2: BizMaster（子テーブルのデータ）を作成する
    biz = BizMaster(
        client_code="TEST",  # 先ほど作成した顧客コードを指定
        business_seq=1,
        name="日次バッチ処理",
        description="毎日実行する処理"
    )
    db_session.add(biz)
    
    # DB側で Computed カラムの自動計算を走らせる
    db_session.flush() 

    # ステップ3: オブジェクトをリフレッシュし、DBで自動計算された最新の値を反映させる
    db_session.refresh(biz)

    # ステップ4: アサーション（各カラム、特に Computed カラムが想定通りに機能しているか検証）
    assert biz.id is not None
    assert biz.client_code == "TEST"
    assert biz.business_seq == 1
    
    # PostgreSQLの Computed("client_code || LPAD(business_seq::text, 4, '0')") 機構が
    # 正常に動作し、「TEST0001」が生成されていることを確認
    assert biz.business_code == "TEST0001"