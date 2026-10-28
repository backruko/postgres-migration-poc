import { EditOutlined, PlusOutlined, UserSwitchOutlined } from "@ant-design/icons";
import { Alert, App, Button, Empty, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo, useState } from "react";
import { staffApi } from "../../domain/staff/api";
import { useCurrentStaff, useRoles, useStaffList } from "../../domain/staff/hooks/useStaff";
import { canManageStaff, canReadStaff, STATUS_COLOR, STATUS_LABEL } from "../../domain/staff/lib/permissions";
import type { Staff } from "../../domain/staff/types";
import { setActingStaffId } from "../../shared/api/actingStaff";
import { ApiError } from "../../shared/api/http";
import { useActingStaffId } from "../../shared/hooks/useActingStaffId";
import { dayjs } from "../../shared/lib/datetime";
import { StaffFormModal } from "./StaffFormModal";
import { isSelf, statusActions, type StatusAction } from "./staffForm";

/**
 * 職員の設定画面。職員の一覧・登録・編集（氏名・メールアドレス・ロール）・有効化／利用停止／再開。
 * 一覧はシステム管理者・業務管理者・監査担当が見られ、登録・変更はシステム管理者だけができる（バックエンドが判定する）。
 * ログインが入るまでは、ナビの「操作する職員」で選んだ職員として操作する。
 */
export function StaffSettingsPage() {
  const { message, modal } = App.useApp();
  const actingId = useActingStaffId();
  const me = useCurrentStaff();
  const canRead = canReadStaff(me.data);
  const canManage = canManageStaff(me.data);
  const staff = useStaffList(canRead);
  const roles = useRoles(me.data?.status === "active");
  const roleName = useMemo(() => new Map((roles.data ?? []).map((r) => [r.code, r.name])), [roles.data]);

  // 登録・編集モーダル（staff が null なら登録）。閉じる間の表示が切り替わらないよう、閉じても対象は残す
  const [editor, setEditor] = useState<{ open: boolean; staff: Staff | null }>({ open: false, staff: null });

  const reloadAll = () => {
    staff.reload();
    me.reload();
  };

  const confirmStatus = (s: Staff, action: StatusAction) =>
    modal.confirm({
      title: `「${s.name}」を${action.label}しますか？`,
      content: action.confirm,
      okText: action.label,
      okButtonProps: { danger: action.danger },
      cancelText: "キャンセル",
      onOk: async () => {
        try {
          const updated = await staffApi.setStatus(s.id, action.to);
          message.success(`「${updated.name}」を${STATUS_LABEL[updated.status]}にしました。`);
        } catch (e) {
          message.error(`変更できませんでした: ${e instanceof ApiError ? e.message : "通信に失敗しました。"}`);
        }
        staff.reload();
      },
    });

  const columns: ColumnsType<Staff> = [
    { title: "ログインID", dataIndex: "login_id", className: "mono" },
    {
      title: "氏名",
      dataIndex: "name",
      render: (name: string, s) => (
        <Space size={6}>
          {name}
          {isSelf(s, me.data) && <Tag color="blue">自分</Tag>}
        </Space>
      ),
    },
    { title: "メールアドレス", dataIndex: "email", render: (v: string | null) => v ?? <span className="muted">—</span> },
    {
      title: "状態",
      dataIndex: "status",
      width: 112,
      render: (v: Staff["status"]) => <Tag color={STATUS_COLOR[v]}>{STATUS_LABEL[v]}</Tag>,
    },
    {
      title: "ロール",
      dataIndex: "roles",
      render: (codes: Staff["roles"]) =>
        codes.length ? (
          <Space size={[4, 4]} wrap>
            {codes.map((c) => (
              <Tag key={c}>{roleName.get(c) ?? c}</Tag>
            ))}
          </Space>
        ) : (
          <span className="muted">なし</span>
        ),
    },
    { title: "登録日", dataIndex: "created_at", width: 112, className: "mono", render: (v: string) => dayjs(v).format("YYYY/M/D") },
    {
      title: "操作",
      key: "actions",
      width: canManage ? 300 : 180,
      render: (_, s) => (
        <Space size={0} wrap>
          {canManage && (
            <Button type="link" size="small" icon={<EditOutlined />} onClick={() => setEditor({ open: true, staff: s })}>
              編集
            </Button>
          )}
          {canManage &&
            statusActions(s).map((a) => (
              <Button
                key={a.to}
                type="link"
                size="small"
                danger={a.danger}
                disabled={isSelf(s, me.data)}
                title={isSelf(s, me.data) ? "自分の状態は変えられません" : undefined}
                onClick={() => confirmStatus(s, a)}
              >
                {a.label}
              </Button>
            ))}
          {!isSelf(s, me.data) && (
            <Button type="link" size="small" icon={<UserSwitchOutlined />} onClick={() => setActingStaffId(s.id)} title="ログインが入るまでの開発用">
              この職員として操作
            </Button>
          )}
        </Space>
      ),
    },
  ];

  const rows = staff.data ?? [];

  return (
    <div className="wrap">
      <header className="page-header">
        <div>
          <div className="eyebrow">事前設定</div>
          <h1>職員</h1>
        </div>
      </header>

      {actingId === null ? (
        <Alert
          type="info"
          showIcon
          message="操作する職員が選ばれていません"
          description="画面上部のナビの「操作する職員」で、職員 ID（最初のシステム管理者は scripts/create_staff.py で登録）を指定してください。"
        />
      ) : me.error ? (
        <Alert
          type="error"
          showIcon
          message={me.error.status === 401 ? `職員 ID ${actingId} は登録されていません` : `職員を確認できませんでした: ${me.error.message}`}
          description="画面上部のナビの「操作する職員」で切り替えてください。"
          action={<Button size="small" onClick={me.reload}>再読み込み</Button>}
        />
      ) : me.data && me.data.status !== "active" ? (
        <Alert type="warning" showIcon message={`「${me.data.name}」は${STATUS_LABEL[me.data.status]}のため、操作できません`} />
      ) : me.data && !canRead ? (
        <Alert type="warning" showIcon message="職員の一覧は、システム管理者・業務管理者・監査担当だけが見られます" />
      ) : null}

      {staff.error && (
        <Alert type="error" showIcon message={staff.error.message} action={<Button size="small" onClick={staff.reload}>再読み込み</Button>} />
      )}

      {canRead && (
        <>
          <div className="table-head">
            <Typography.Text type="secondary">
              職員 {rows.length} 名（有効 {rows.filter((s) => s.status === "active").length} 名）
              {!canManage && " ・ 登録・変更はシステム管理者だけができます"}
            </Typography.Text>
            {canManage && (
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditor({ open: true, staff: null })} disabled={!roles.data}>
                職員を登録
              </Button>
            )}
          </div>
          <Table<Staff>
            rowKey="id"
            columns={columns}
            dataSource={rows}
            loading={staff.loading}
            pagination={false}
            size="middle"
            scroll={{ x: 960 }}
            rowClassName={(s) => (s.status === "active" ? "" : "is-past-row")}
            locale={{ emptyText: <Empty description="職員が登録されていません" /> }}
          />
          {canManage && (
            <StaffFormModal
              open={editor.open}
              staff={editor.staff}
              me={me.data}
              roles={roles.data ?? []}
              onClose={() => setEditor((s) => ({ ...s, open: false }))}
              onSaved={reloadAll}
            />
          )}
        </>
      )}
    </div>
  );
}
