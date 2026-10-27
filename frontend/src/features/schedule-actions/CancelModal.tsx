import { Descriptions, Form, Input, Modal } from "antd";
import { useEffect } from "react";
import { instanceApi } from "../../domain/plan/api";
import type { ClientOccurrence } from "../../domain/plan/types";
import { inZone, timeIn } from "../../shared/lib/datetime";
import { useSubmit } from "./useSubmit";

interface Props {
  occurrence: ClientOccurrence | null;
  timezone: string;
  onClose: () => void;
  onDone: () => void;
}

/** 予定の取消（1件だけ）。以後テンプレートやカレンダーが変わっても、この予定は復活しない */
export function CancelModal({ occurrence, timezone, onClose, onDone }: Props) {
  const [form] = Form.useForm<{ note?: string }>();
  const { submitting, submit } = useSubmit(() => {
    onClose();
    onDone();
  });

  useEffect(() => {
    if (occurrence) form.resetFields();
  }, [occurrence, form]);

  const onFinish = ({ note }: { note?: string }) => {
    if (!occurrence) return;
    submit(() => instanceApi.cancel(occurrence.instance_id, { note: note || null }), "予定を取り消しました");
  };

  return (
    <Modal
      open={occurrence !== null}
      title="予定を取り消す"
      okText="取り消す"
      okButtonProps={{ danger: true }}
      cancelText="やめる"
      onOk={() => form.submit()}
      onCancel={onClose}
      confirmLoading={submitting}
      forceRender
    >
      {occurrence && (
        <Descriptions
          size="small"
          column={1}
          className="modal-alert"
          items={[
            { key: "title", label: "予定", children: `${occurrence.title}（${occurrence.biz_code} ${occurrence.biz_name}）` },
            {
              key: "when",
              label: "日時",
              children: `${inZone(occurrence.starts_at, timezone).format("YYYY/MM/DD (dd)")} ${timeIn(occurrence.starts_at, timezone)}–${timeIn(occurrence.ends_at, timezone)}`,
            },
          ]}
        />
      )}
      <p className="modal-note">
        この1件だけ取り消します。テンプレートのルールを変更しても復活しません（「元に戻す」は変更した予定のみ）。
      </p>
      <Form form={form} layout="vertical" onFinish={onFinish}>
        <Form.Item name="note" label="取消理由">
          <Input.TextArea rows={2} maxLength={500} showCount placeholder="例: 顧客都合により中止" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
