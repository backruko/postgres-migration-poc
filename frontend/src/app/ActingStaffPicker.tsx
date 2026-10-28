import { UserOutlined } from "@ant-design/icons";
import { Button, InputNumber, Popover, Space, Tag, Typography } from "antd";
import { useState } from "react";
import { useCurrentStaff } from "../domain/staff/hooks/useStaff";
import { STATUS_COLOR, STATUS_LABEL } from "../domain/staff/lib/permissions";
import { setActingStaffId } from "../shared/api/actingStaff";
import { useActingStaffId } from "../shared/hooks/useActingStaffId";

/**
 * ナビの「操作する職員」。ログインが入るまでの開発用で、選んだ職員 ID を X-Staff-Id として全リクエストに付ける。
 * ログインを入れたら、ログイン中の職員の表示（とログアウト）に置き換える。
 */
export function ActingStaffPicker() {
  const actingId = useActingStaffId();
  const me = useCurrentStaff();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<number | null>(actingId);

  const label =
    actingId === null ? "操作する職員: 未選択" : me.data ? me.data.name : me.error ? `職員 ID ${actingId}（未登録）` : `職員 ID ${actingId}`;

  const apply = (id: number | null) => {
    setActingStaffId(id);
    setOpen(false);
  };

  const content = (
    <Space direction="vertical" size={10} className="acting-staff-panel">
      {me.data && (
        <div>
          <Typography.Text strong>{me.data.name}</Typography.Text> <span className="muted mono">{me.data.login_id}</span>
          <div>
            <Tag color={STATUS_COLOR[me.data.status]}>{STATUS_LABEL[me.data.status]}</Tag>
            {me.data.roles.length ? me.data.roles.map((r) => <Tag key={r}>{r}</Tag>) : <span className="muted">ロールなし</span>}
          </div>
        </div>
      )}
      {me.error && <Typography.Text type="danger">{me.error.message}</Typography.Text>}
      <Space.Compact>
        <InputNumber<number>
          min={1}
          precision={0}
          placeholder="職員 ID"
          value={draft}
          onChange={(v) => setDraft(v)}
          onPressEnter={() => apply(draft)}
          aria-label="職員 ID"
        />
        <Button type="primary" onClick={() => apply(draft)} disabled={draft === null || draft === actingId}>
          切り替え
        </Button>
      </Space.Compact>
      <Typography.Text type="secondary" className="acting-staff-note">
        ログインが入るまでの開発用です。選んだ職員としてすべての操作が行われます。
      </Typography.Text>
      {actingId !== null && (
        <Button size="small" onClick={() => apply(null)}>
          選択を解除
        </Button>
      )}
    </Space>
  );

  return (
    <Popover
      content={content}
      title="操作する職員"
      trigger="click"
      placement="bottomRight"
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setDraft(actingId);
          if (actingId !== null) me.reload(); // 職員画面で状態・ロールが変わっていても最新を表示する
        }
      }}
    >
      <button type="button" className={`acting-staff${me.error || actingId === null ? " is-warning" : ""}`}>
        <UserOutlined aria-hidden="true" />
        <span className="acting-staff-name">{label}</span>
        {me.data && me.data.status !== "active" && <Tag color={STATUS_COLOR[me.data.status]}>{STATUS_LABEL[me.data.status]}</Tag>}
      </button>
    </Popover>
  );
}
