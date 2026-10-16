from sqlalchemy import select

stmt = (
    select(SOPVersion)
    .where(SOPVersion.biz_master_id == 1, SOPVersion.status == "active")
    .order_by(SOPVersion.version_number.desc())  # 💡 整数なので確実に最新が上にくる！
    .limit(1)
)
latest_version = db.scalars(stmt).first()

print(latest_version.version_tag)  # ➔ 画面には綺麗に "v2.1.0" と表示できる