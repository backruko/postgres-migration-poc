from pydantic import BaseModel, ConfigDict

# ==========================================
# 1. Base（共通属性）
# ここには「作成時」や「取得時」に必ず存在する【必須の基本項目】だけを定義します。
# ==========================================
class ClientBase(BaseModel):
    code: str
    name: str

# ==========================================
# 2. Create（作成用スキーマ）
# Baseを継承します。作成時に新しく必要な項目があればここに追加します。
# （パスワードなど、取得時には返さないが作成時に必要なもの等）
# ==========================================
class ClientCreate(ClientBase):
    pass

# ==========================================
# 3. Update（更新用スキーマ）
# 【重要】Baseを継承しません。
# PATCH（部分更新）の意図を明確にするため、あえて独立させ、全て Optional で定義します。
# これにより、型検査も通り、意図しない上書きバグも防げます。
# ==========================================
class ClientUpdate(BaseModel):
    code: str | None = None
    name: str | None = None

# ==========================================
# 4. Response（レスポンス用スキーマ）
# DBから取得したデータをフロントに返す形です。Baseの必須項目に加え、IDなどを持ちます。
# ==========================================
class ClientResponse(ClientBase):
    id: int
    
    # ORMモデル（SQLAlchemy等）からの変換を許可する厳格な設定
    model_config = ConfigDict(from_attributes=True)