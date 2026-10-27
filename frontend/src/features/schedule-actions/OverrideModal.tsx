import { Alert, Form, Input, Modal } from "antd";
import { useEffect } from "react";
import { instanceApi } from "../../domain/plan/api";
import type { ClientOccurrence } from "../../domain/plan/types";
import { inZone, zonedISO } from "../../shared/lib/datetime";
import { ScheduleFields, type ScheduleFormValues } from "./ScheduleFields";
import { useSubmit } from "./useSubmit";

interface FormValues extends ScheduleFormValues {
  note?: string;
}

interface Props {
  occurrence: ClientOccurrence | null;
  timezone: string;
  onClose: () => void;
  onDone: () => void;
}

/**
 * 予定の変更（1件だけ日時を変える）。
 * 元の予定は「手動変更」として無効化され、新しい日時の予定が追加される。テンプレートのルールは変わらない。
 */
export function OverrideModal({ occurrence, timezone, onClose, onDone }: Props) {
  const [form] = Form.useForm<FormValues>();
  const { submitting, submit } = useSubmit(() => {
    onClose();
    onDone();
  });

  useEffect(() => {
    if (!occurrence) return;
    const start = inZone(occurrence.starts_at, timezone);
    form.setFieldsValue({
      date: start,
      start,
      end: inZone(occurrence.ends_at, timezone),
      bg_color: undefined,
      note: "",
    });
  }, [occurrence, timezone, form]);

  const onFinish = (v: FormValues) => {
    if (!occurrence) return;
    submit(
      () =>
        instanceApi.override(occurrence.instance_id, {
          scheduled_start: zonedISO(v.date, v.start, timezone),
          scheduled_end: zonedISO(v.date, v.end, timezone),
          // 色を選ばなければ送らない（元の予定の表示設定を引き継ぐ）
          ...(v.bg_color ? { ui_metadata: { bg_color: v.bg_color } } : {}),
          note: v.note || null,
        }),
      "予定を変更しました",
    );
  };

  return (
    <Modal
      open={occurrence !== null}
      title="予定を変更"
      okText="変更する"
      cancelText="キャンセル"
      onOk={() => form.submit()}
      onCancel={onClose}
      confirmLoading={submitting}
      forceRender
    >
      {occurrence && (
        <Alert
          type="info"
          showIcon
          className="modal-alert"
          message={`${occurrence.title}（${occurrence.biz_code}）`}
          description="この1件だけ日時を変更します。テンプレートのルールやほかの日の予定は変わりません。"
        />
      )}
      <Form form={form} layout="vertical" onFinish={onFinish} requiredMark="optional">
        <ScheduleFields timezone={timezone} colorHelp="選ばない場合は元の予定の表示を引き継ぎます" />
        <Form.Item name="note" label="変更理由">
          <Input.TextArea rows={2} maxLength={500} showCount placeholder="例: 顧客の依頼により延期" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
