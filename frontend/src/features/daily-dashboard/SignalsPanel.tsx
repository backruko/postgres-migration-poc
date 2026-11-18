import { Alert, App, Button, Empty, Form, Input, InputNumber, Modal, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useState } from "react";
import { signalApi } from "../../domain/ope/api/signalApi";
import { canHandleSignals, SIGNAL_RESULT_LABEL } from "../../domain/ope/lib/signals";
import type { Signal } from "../../domain/ope/types";
import { useCurrentStaff } from "../../domain/staff/hooks/useStaff";
import { ApiError } from "../../shared/api/http";
import { TIMEZONE } from "../../shared/config";
import { inZone } from "../../shared/lib/datetime";

interface Props {
  /** 人の対応が必要な信号（ページで取得する: タブの件数にも使う）。ログインしている職員が有効でないときは取得しない */
  signals: { data: Signal[] | null; error: ApiError | null; reload: () => void };
  /** ログインしている職員が有効か（無効なら一覧を出さない） */
  active: boolean;
  /** 対応でオーダーが変わったとき（ダッシュボードを取り直す） */
  onChanged: () => void;
}

interface Values {
  note: string;
  order_id?: number | null;
}

/**
 * 照合できなかった信号（人の対応が必要なもの）。日付に関係なく、未対応のものすべてを日次ダッシュボードの上に出す。
 * 対応: オーダー番号を入れるとそのオーダーを完了にしてから対応済みに、入れなければ対応済みにするだけ（誤送信など）。
 */
export function SignalsPanel({ signals, active, onChanged }: Props) {
  const { message } = App.useApp();
  const me = useCurrentStaff();
  const canHandle = canHandleSignals(me.data);
  const [form] = Form.useForm<Values>();
  const [target, setTarget] = useState<Signal | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const rows = signals.data ?? [];
  if (!active) {
    return <Empty description="このアカウントでは信号を見られません（利用開始前・停止中）" />;
  }

  const open = (s: Signal) => {
    form.resetFields();
    setError(null);
    setTarget(s);
  };

  const save = async (v: Values) => {
    if (!target) return;
    setSaving(true);
    setError(null);
    try {
      await signalApi.handle(target.id, v.note.trim(), v.order_id ?? null);
      message.success(v.order_id ? `オーダー #${v.order_id} を完了にし、信号を対応済みにしました` : "信号を対応済みにしました");
      setTarget(null);
      signals.reload();
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "対応できませんでした。");
    } finally {
      setSaving(false);
    }
  };

  const columns: ColumnsType<Signal> = [
    { title: "受信", dataIndex: "received_at", width: 128, className: "mono", render: (v: string) => inZone(v, TIMEZONE).format("M/D HH:mm") },
    { title: "顧客", dataIndex: "client_code", width: 72, className: "mono" },
    { title: "信号の種類", dataIndex: "signal_type", className: "mono" },
    { title: "キー", dataIndex: "key", width: 112, className: "mono" },
    {
      title: "結果",
      key: "result",
      render: (_, s) => (
        <Space size={4} wrap>
          <Tag color="orange">{SIGNAL_RESULT_LABEL[s.result]}</Tag>
          <span className="muted">{s.result_note}</span>
        </Space>
      ),
    },
    ...(canHandle
      ? [{
          title: "",
          key: "actions",
          width: 72,
          render: (_: unknown, s: Signal) => <Button size="small" onClick={() => open(s)}>対応</Button>,
        }]
      : []),
  ];

  return (
    <section className="signals-panel" aria-label="照合できなかった信号">
      <Typography.Text type="secondary">照合できなかった信号（日付によらず、人の対応が必要なもの）</Typography.Text>
      {signals.error && <Alert type="error" showIcon message={`信号を取得できませんでした: ${signals.error.message}`} />}
      <Table<Signal>
        rowKey="id"
        columns={columns}
        dataSource={rows}
        pagination={false}
        size="small"
        scroll={{ x: 760 }}
        locale={{ emptyText: <Empty description="対応が必要な信号はありません" /> }}
      />

      <Modal
        open={target !== null}
        title="照合できなかった信号に対応"
        okText="対応済みにする"
        cancelText="キャンセル"
        onOk={() => form.submit()}
        onCancel={() => setTarget(null)}
        confirmLoading={saving}
        destroyOnClose
      >
        {target && (
          <Typography.Paragraph type="secondary">
            <span className="mono">{target.client_code} ・ {target.signal_type} ・ {target.key}</span>
            <br />
            {target.result_note}
          </Typography.Paragraph>
        )}
        {error && <Alert type="error" showIcon message={error} className="modal-alert" />}
        <Form form={form} layout="vertical" onFinish={save} disabled={saving}>
          <Form.Item name="order_id" label="完了にするオーダー番号（任意。ready・作業中のオーダー）">
            <InputNumber min={1} className="mono" placeholder="例: 194" />
          </Form.Item>
          <Form.Item name="note" label="対応の内容" rules={[{ required: true, whitespace: true, message: "対応の内容を入力してください" }, { max: 500 }]}>
            <Input.TextArea rows={2} maxLength={500} placeholder="例: リリース後に受領を確認 / 誤送信" />
          </Form.Item>
        </Form>
      </Modal>
    </section>
  );
}
