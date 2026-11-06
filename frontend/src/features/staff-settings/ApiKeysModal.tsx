import { Alert, App, Button, Form, Input, Modal, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";
import { staffApi } from "../../domain/staff/api";
import type { ApiKey, ApiKeyIssued, Staff } from "../../domain/staff/types";
import { TIMEZONE } from "../../shared/config";
import { ApiError } from "../../shared/api/http";
import { useApiQuery } from "../../shared/hooks/useApiQuery";
import { inZone } from "../../shared/lib/datetime";

interface Props {
  open: boolean;
  staff: Staff | null;
  onClose: () => void;
}

const when = (iso: string | null) => (iso ? inZone(iso, TIMEZONE).format("YYYY/M/D HH:mm") : "—");

/**
 * 信号の送信（signal_sender）の職員の API キー（システム管理者）。信号を送るスクリプトが Authorization: Bearer で使う。
 * キーは発行したときにだけ表示する（保存されるのはハッシュ）。漏れたら取り消して発行し直す。
 */
export function ApiKeysModal({ open, staff, onClose }: Props) {
  const { message, modal } = App.useApp();
  const keys = useApiQuery((signal) => staffApi.listApiKeys(staff!.id, signal), [staff?.id, open], { enabled: open && staff !== null });
  const [label, setLabel] = useState("");
  const [issuing, setIssuing] = useState(false);
  const [issued, setIssued] = useState<ApiKeyIssued | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setLabel("");
      setIssued(null);
      setError(null);
    }
  }, [open]);

  const issue = async () => {
    if (!staff) return;
    setIssuing(true);
    setError(null);
    try {
      const created = await staffApi.issueApiKey(staff.id, label.trim());
      setIssued(created);
      setLabel("");
      keys.reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "発行できませんでした。");
    } finally {
      setIssuing(false);
    }
  };

  const revoke = (k: ApiKey) =>
    modal.confirm({
      title: `API キー「${k.label}」（${k.key_prefix}…）を取り消しますか？`,
      content: "このキーを使っているスクリプトは、すぐに信号を送れなくなります。",
      okText: "取り消す",
      okButtonProps: { danger: true },
      cancelText: "キャンセル",
      onOk: async () => {
        try {
          await staffApi.revokeApiKey(k.id);
          message.success("API キーを取り消しました。");
        } catch (e) {
          message.error(`取り消せませんでした: ${e instanceof ApiError ? e.message : "通信に失敗しました。"}`);
        }
        keys.reload();
      },
    });

  const columns: ColumnsType<ApiKey> = [
    { title: "用途", dataIndex: "label" },
    { title: "キー", dataIndex: "key_prefix", className: "mono", render: (v: string) => `${v}…` },
    { title: "発行", dataIndex: "created_at", className: "mono", render: when },
    { title: "最後に使用", dataIndex: "last_used_at", className: "mono", render: when },
    {
      title: "",
      key: "state",
      width: 110,
      render: (_, k) => (k.revoked_at ? <Tag>取消済み</Tag> : <Button type="link" size="small" danger onClick={() => revoke(k)}>取り消す</Button>),
    },
  ];

  return (
    <Modal open={open} title={staff ? `API キー（${staff.name}）` : "API キー"} footer={null} onCancel={onClose} width={720} destroyOnClose>
      <Typography.Paragraph type="secondary">
        信号を送るスクリプトが <span className="mono">Authorization: Bearer &lt;キー&gt;</span> で POST /ope/signals に使います。ほかの API には使えません。
      </Typography.Paragraph>
      {issued && (
        <Alert
          type="success"
          showIcon
          style={{ marginBottom: 12 }}
          message="API キーを発行しました。この画面を閉じると二度と表示されません。いまコピーしてください。"
          description={<Typography.Text className="mono api-key-value" copyable>{issued.key}</Typography.Text>}
        />
      )}
      <Form layout="inline" onFinish={() => void issue()} style={{ marginBottom: 12 }}>
        <Form.Item label="用途">
          <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="例: SFTP 監視" maxLength={100} style={{ width: 240 }} />
        </Form.Item>
        <Space>
          <Button type="primary" htmlType="submit" loading={issuing} disabled={!label.trim()}>発行</Button>
        </Space>
      </Form>
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} />}
      {keys.error && <Alert type="error" showIcon message={keys.error.message} style={{ marginBottom: 12 }} />}
      <Table<ApiKey> rowKey="id" columns={columns} dataSource={keys.data ?? []} loading={keys.loading} pagination={false} size="small"
        rowClassName={(k) => (k.revoked_at ? "is-past-row" : "")} />
    </Modal>
  );
}
