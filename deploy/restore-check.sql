-- 復元の練習（GO-LIVE.md の A）で、元のデータベースと復元したデータベースの件数を比べるための問い合わせ。
-- 2 つの結果が同じなら、バックアップから元どおりに戻せている。
--   docker compose exec -T db psql -X -A -t -U ops -d ops < restore-check.sql
SELECT 'alembic ' || (SELECT version_num FROM alembic_version)
UNION ALL SELECT 'staff_user ' || count(*) FROM staff_user
UNION ALL SELECT 'client_master ' || count(*) FROM client_master
UNION ALL SELECT 'biz_master ' || count(*) FROM biz_master
UNION ALL SELECT 'plan_template ' || count(*) FROM plan_template
UNION ALL SELECT 'ope_order ' || count(*) FROM ope_order
UNION ALL SELECT 'ope_order_event ' || count(*) FROM ope_order_event;
