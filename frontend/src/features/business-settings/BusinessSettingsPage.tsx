import { EditOutlined, PlusOutlined, SettingOutlined } from "@ant-design/icons";
import { Alert, App, Button, Empty, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { bizApi } from "../../domain/biz/api/bizApi";
import { canManageBusinesses, nextBusinessCode } from "../../domain/biz/lib/businesses";
import type { BizMaster } from "../../domain/biz/types";
import { useClients } from "../../domain/client/hooks/useClients";
import { useCurrentStaff } from "../../domain/staff/hooks/useStaff";
import { ApiError } from "../../shared/api/http";
import { useApiQuery } from "../../shared/hooks/useApiQuery";
import { BizFormModal } from "./BizFormModal";

/**
 * 業務の設定画面（顧客ごと）。一覧（削除した業務も含む）・登録・編集（業務名・管理者メール・説明）・削除・元に戻す。
 * 見るのは誰でも。変更は業務管理者だけ（バックエンドが判定する）。業務コードは自動で付き、削除した業務の番号も使い回さない。
 * 削除は、予定テンプレート・未完了のオーダー・依存関係が残っている間はできない（バックエンドが理由を返す）。
 * リリース・顧客側の出来事の設定と予定テンプレートは、予定テンプレート設定の画面（?biz=）で扱う。
 * 選択した顧客は URL の ?client= に保持する。
 */
export function BusinessSettingsPage() {
  const { message, modal } = App.useApp();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const clientCode = params.get("client")?.toUpperCase() || null;
  const clients = useClients();
  const me = useCurrentStaff();
  const canManage = canManageBusinesses(me.data);
  const bizes = useApiQuery(
    (signal) => bizApi.listForClient(clientCode as string, true, signal), [clientCode], { enabled: clientCode !== null },
  );

  const client = (clients.data ?? []).find((c) => c.code === clientCode) ?? null;
  const rows = bizes.data ?? [];
  const nextCode = useMemo(() => (clientCode ? nextBusinessCode(clientCode, rows) : null), [clientCode, rows]);

  // 登録・編集モーダル（biz が null なら登録）。閉じる間の表示が切り替わらないよう、閉じても対象は残す
  const [editor, setEditor] = useState<{ open: boolean; biz: BizMaster | null }>({ open: false, biz: null });

  const selectClient = (code: string | undefined) => {
    const next = new URLSearchParams(params);
    if (code) next.set("client", code);
    else next.delete("client");
    setParams(next, { replace: true });
  };

  const confirmRemove = (b: BizMaster) =>
    modal.confirm({
      title: `業務 ${b.business_code}「${b.name}」を削除しますか？`,
      content:
        "予定テンプレート・未完了のオーダー・依存関係が残っている間は削除できません（先にそれらを削除・取消してください）。" +
        "削除した業務は「元に戻す」で戻せます。業務コードは使い回しません。",
      okText: "削除",
      okButtonProps: { danger: true },
      cancelText: "キャンセル",
      onOk: async () => {
        try {
          await bizApi.remove(b.id);
          message.success(`業務 ${b.business_code} を削除しました。`);
        } catch (e) {
          message.error(`削除できませんでした: ${e instanceof ApiError ? e.message : "通信に失敗しました。"}`, 10);
        }
        bizes.reload();
      },
    });

  const restore = async (b: BizMaster) => {
    try {
      await bizApi.restore(b.id);
      message.success(`業務 ${b.business_code} を元に戻しました。`);
    } catch (e) {
      message.error(`元に戻せませんでした: ${e instanceof ApiError ? e.message : "通信に失敗しました。"}`, 8);
    }
    bizes.reload();
  };

  const columns: ColumnsType<BizMaster> = [
    { title: "業務コード", dataIndex: "business_code", width: 120, className: "mono" },
    {
      title: "業務名",
      dataIndex: "name",
      render: (name: string, b) => (
        <div>
          {name}
          {b.description && <div className="muted biz-description">{b.description}</div>}
        </div>
      ),
    },
    { title: "管理者メール", dataIndex: "manager_email", render: (v: string | null) => v ?? <span className="muted">—</span> },
    {
      title: "状態",
      dataIndex: "is_deleted",
      width: 96,
      render: (deleted: boolean) => (deleted ? <Tag>削除済み</Tag> : <Tag color="green">有効</Tag>),
    },
    {
      title: "操作",
      key: "actions",
      width: canManage ? 360 : 220,
      render: (_, b) => (
        <Space size={0} wrap>
          {!b.is_deleted && (
            <Button type="link" size="small" icon={<SettingOutlined />} onClick={() => navigate({ pathname: "/settings/templates", search: `?biz=${b.id}` })}>
              予定テンプレート・業務の設定
            </Button>
          )}
          {canManage && !b.is_deleted && (
            <Button type="link" size="small" icon={<EditOutlined />} onClick={() => setEditor({ open: true, biz: b })}>
              編集
            </Button>
          )}
          {canManage && !b.is_deleted && (
            <Button type="link" size="small" danger onClick={() => confirmRemove(b)}>
              削除
            </Button>
          )}
          {canManage && b.is_deleted && (
            <Button type="link" size="small" onClick={() => void restore(b)} disabled={!client} title={client ? undefined : "無効な顧客の業務は元に戻せません"}>
              元に戻す
            </Button>
          )}
        </Space>
      ),
    },
  ];

  const aliveCount = rows.filter((b) => !b.is_deleted).length;

  return (
    <div className="wrap">
      <header className="page-header">
        <div>
          <div className="eyebrow">事前設定</div>
          <h1>業務</h1>
        </div>
      </header>

      <label className="field">
        <span className="field-label">顧客</span>
        <Select<string>
          id="client"
          showSearch
          allowClear
          placeholder="顧客を選択（顧客コード・顧客名で検索）"
          value={clientCode ?? undefined}
          options={(clients.data ?? []).map((c) => ({ value: c.code, label: `${c.code} ${c.name}` }))}
          optionFilterProp="label"
          loading={clients.loading}
          onChange={selectClient}
          notFoundContent={clients.loading ? "読み込み中…" : "該当する顧客がありません"}
          style={{ width: "min(420px, 100%)" }}
        />
      </label>

      {clients.error && <Alert type="error" showIcon message={clients.error.message} />}
      {bizes.error && (
        <Alert type="error" showIcon message={bizes.error.message} action={<Button size="small" onClick={bizes.reload}>再読み込み</Button>} />
      )}

      {clientCode === null ? (
        <Empty description="顧客を選択してください" />
      ) : (
        <>
          {clients.data && !client && (
            <Alert type="warning" showIcon message={`顧客 ${clientCode} は無効か、登録されていません。業務の登録・元に戻すはできません。`} style={{ marginBottom: 12 }} />
          )}
          <div className="table-head">
            <Typography.Text type="secondary">
              業務 {aliveCount} 件{rows.length > aliveCount && `（ほかに削除済み ${rows.length - aliveCount} 件）`}
              {!canManage && " ・ 変更は業務管理者だけができます"}
            </Typography.Text>
            {canManage && (
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditor({ open: true, biz: null })} disabled={!client || !bizes.data}>
                業務を登録
              </Button>
            )}
          </div>
          <Table<BizMaster>
            rowKey="id"
            columns={columns}
            dataSource={rows}
            loading={bizes.loading}
            pagination={false}
            size="middle"
            rowClassName={(b) => (b.is_deleted ? "is-past-row" : "")}
            locale={{ emptyText: <Empty description="業務が登録されていません" /> }}
          />
          {canManage && client && (
            <BizFormModal
              open={editor.open}
              biz={editor.biz}
              clientCode={client.code}
              clientName={client.name}
              nextCode={nextCode}
              onClose={() => setEditor((s) => ({ ...s, open: false }))}
              onSaved={bizes.reload}
            />
          )}
        </>
      )}
    </div>
  );
}
