import { EditOutlined, PlusOutlined } from "@ant-design/icons";
import { Alert, App, Button, Empty, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo, useState } from "react";
import { useBizMasters } from "../../domain/biz/hooks/useBizMasters";
import { clientApi } from "../../domain/client/api/clientApi";
import { useAllClients } from "../../domain/client/hooks/useClients";
import { canManageClients, deactivateBlockedReason } from "../../domain/client/lib/clients";
import type { Client } from "../../domain/client/types";
import { useCurrentStaff } from "../../domain/staff/hooks/useStaff";
import { ApiError } from "../../shared/api/http";
import { dayjs } from "../../shared/lib/datetime";
import { ClientFormModal } from "./ClientFormModal";

/**
 * 顧客の設定画面。一覧（無効にした顧客も含む）・登録・顧客名の変更・無効 / 有効。
 * 見るのは誰でも。登録・変更は業務管理者だけ（バックエンドが判定する）。顧客は削除しない:
 * 使わなくなったら無効にする（有効な業務が残っている間は無効にできない）。無効な顧客はほかの画面の選択肢から消える。
 */
export function ClientSettingsPage() {
  const { message, modal } = App.useApp();
  const me = useCurrentStaff();
  const canManage = canManageClients(me.data);
  const clients = useAllClients();
  const bizes = useBizMasters();

  // 顧客ごとの有効な（削除されていない）業務の数
  const businessCount = useMemo(() => {
    const counts = new Map<string, number>();
    for (const b of bizes.data ?? []) counts.set(b.client_code, (counts.get(b.client_code) ?? 0) + 1);
    return counts;
  }, [bizes.data]);

  // 登録・名前の変更モーダル（client が null なら登録）。閉じる間の表示が切り替わらないよう、閉じても対象は残す
  const [editor, setEditor] = useState<{ open: boolean; client: Client | null }>({ open: false, client: null });

  const confirmActive = (c: Client) => {
    const deactivate = c.is_active;
    modal.confirm({
      title: `顧客 ${c.code}「${c.name}」を${deactivate ? "無効" : "有効"}にしますか？`,
      content: deactivate
        ? "カレンダー・日次ダッシュボード・設定の顧客の選択肢から消え、業務・年間計画シートを新しく作れなくなります。これまでの記録は残ります。"
        : "顧客の選択肢に戻り、業務・年間計画シートを作れるようになります。",
      okText: deactivate ? "無効にする" : "有効にする",
      okButtonProps: { danger: deactivate },
      cancelText: "キャンセル",
      onOk: async () => {
        try {
          const updated = deactivate ? await clientApi.deactivate(c.id) : await clientApi.activate(c.id);
          message.success(`顧客 ${updated.code} を${updated.is_active ? "有効" : "無効"}にしました。`);
        } catch (e) {
          message.error(`変更できませんでした: ${e instanceof ApiError ? e.message : "通信に失敗しました。"}`, 8);
        }
        clients.reload();
        bizes.reload();
      },
    });
  };

  const columns: ColumnsType<Client> = [
    { title: "顧客コード", dataIndex: "code", width: 112, className: "mono" },
    { title: "顧客名", dataIndex: "name" },
    {
      title: "状態",
      dataIndex: "is_active",
      width: 96,
      render: (active: boolean) => (active ? <Tag color="green">有効</Tag> : <Tag>無効</Tag>),
    },
    {
      title: "業務",
      key: "businesses",
      width: 96,
      render: (_, c) => {
        const n = businessCount.get(c.code) ?? 0;
        return n ? <span className="mono">{n} 件</span> : <span className="muted">なし</span>;
      },
    },
    { title: "登録日", dataIndex: "created_at", width: 112, className: "mono", render: (v: string) => dayjs(v).format("YYYY/M/D") },
    ...(canManage
      ? [
          {
            title: "操作",
            key: "actions",
            width: 220,
            render: (_: unknown, c: Client) => {
              const blocked = bizes.data ? deactivateBlockedReason(c, businessCount.get(c.code) ?? 0) : null;
              return (
                <Space size={0} wrap>
                  <Button type="link" size="small" icon={<EditOutlined />} onClick={() => setEditor({ open: true, client: c })}>
                    名前を変更
                  </Button>
                  <Button type="link" size="small" danger={c.is_active} disabled={blocked !== null} title={blocked ?? undefined} onClick={() => confirmActive(c)}>
                    {c.is_active ? "無効にする" : "有効にする"}
                  </Button>
                </Space>
              );
            },
          },
        ]
      : []),
  ];

  const rows = clients.data ?? [];
  const activeCount = rows.filter((c) => c.is_active).length;

  return (
    <div className="wrap">
      <header className="page-header">
        <div>
          <div className="eyebrow">事前設定</div>
          <h1>顧客</h1>
        </div>
      </header>

      {clients.error && (
        <Alert type="error" showIcon message={clients.error.message} action={<Button size="small" onClick={clients.reload}>再読み込み</Button>} />
      )}

      <div className="table-head">
        <Typography.Text type="secondary">
          顧客 {rows.length} 件（有効 {activeCount} 件）
          {!canManage && " ・ 登録・変更は業務管理者だけができます"}
        </Typography.Text>
        {canManage && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditor({ open: true, client: null })} disabled={!clients.data}>
            顧客を登録
          </Button>
        )}
      </div>
      <Table<Client>
        rowKey="id"
        columns={columns}
        dataSource={rows}
        loading={clients.loading}
        pagination={false}
        size="middle"
        rowClassName={(c) => (c.is_active ? "" : "is-past-row")}
        locale={{ emptyText: <Empty description="顧客が登録されていません" /> }}
      />
      {canManage && (
        <ClientFormModal
          open={editor.open}
          client={editor.client}
          existing={rows}
          onClose={() => setEditor((s) => ({ ...s, open: false }))}
          onSaved={clients.reload}
        />
      )}
    </div>
  );
}
