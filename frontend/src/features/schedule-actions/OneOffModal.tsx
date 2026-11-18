import { Alert, Form, Modal, Select } from "antd";
import { useEffect } from "react";
import { instanceApi } from "../../domain/plan/api";
import type { CalendarTemplateInfo } from "../../domain/plan/types";
import { dayjs, zonedISO } from "../../shared/lib/datetime";
import { ScheduleFields, type ScheduleFormValues } from "./ScheduleFields";
import { useSubmit } from "./useSubmit";

interface FormValues extends ScheduleFormValues {
  template_id: number;
}

interface Props {
  /** 開いたときに選択しておく日付（YYYY-MM-DD）。null で閉じる */
  date: string | null;
  templates: CalendarTemplateInfo[];
  timezone: string;
  onClose: () => void;
  onDone: () => void;
}

/** テンプレートの既定の開始時刻・所要時間 */
function defaultTimes(t: CalendarTemplateInfo) {
  const start = dayjs(t.start_time, "HH:mm:ss");
  return { start, end: start.add(t.duration_minutes, "minute") };
}

/**
 * 臨時の予定の追加（特例対応など、ルールに無い日時に1件だけ予定を入れる）。
 * テンプレートのルールを変更しても維持される。
 */
export function OneOffModal({ date, templates, timezone, onClose, onDone }: Props) {
  const [form] = Form.useForm<FormValues>();
  const { submitting, submit } = useSubmit(() => {
    onClose();
    onDone();
  });

  useEffect(() => {
    if (date === null) return;
    const first = templates[0];
    form.setFieldsValue({
      template_id: first?.template_id,
      date: dayjs(date),
      ...(first ? defaultTimes(first) : {}),
      bg_color: undefined,
    });
  }, [date, templates, form]);

  // 業務を選び直したら、その業務の既定の時刻に合わせる
  const onTemplateChange = (templateId: number) => {
    const t = templates.find((x) => x.template_id === templateId);
    if (t) form.setFieldsValue(defaultTimes(t));
  };

  const onFinish = (v: FormValues) => {
    submit(
      () =>
        instanceApi.addOneOff(v.template_id, {
          scheduled_start: zonedISO(v.date, v.start, timezone),
          scheduled_end: zonedISO(v.date, v.end, timezone),
          ...(v.bg_color ? { ui_metadata: { bg_color: v.bg_color } } : {}),
        }),
      "臨時の予定を追加しました",
    );
  };

  return (
    <Modal
      open={date !== null}
      title="臨時の予定を追加"
      okText="追加する"
      cancelText="キャンセル"
      onOk={() => form.submit()}
      onCancel={onClose}
      confirmLoading={submitting}
      forceRender
    >
      <Alert
        type="info"
        showIcon
        className="modal-alert"
        message="特例対応など、ルールに無い日時に1件だけ予定を追加します。"
        description="テンプレートのルールを変更しても、この予定はそのまま残ります。"
      />
      <Form form={form} layout="vertical" onFinish={onFinish} requiredMark="optional">
        <Form.Item name="template_id" label="業務" rules={[{ required: true, message: "業務を選択してください" }]}>
          <Select
            showSearch
            optionFilterProp="label"
            onChange={onTemplateChange}
            options={templates.map((t) => ({ value: t.template_id, label: `${t.name}（${t.biz_code} ${t.biz_name}）` }))}
          />
        </Form.Item>
        <ScheduleFields timezone={timezone} colorHelp="選ばない場合は業務の表示色になります" />
      </Form>
    </Modal>
  );
}
