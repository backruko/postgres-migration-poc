import { Alert, App, DatePicker, Form, Modal, Typography } from "antd";
import { useEffect, useState } from "react";
import { sheetApi } from "../../domain/plan/api";
import type { ReferenceSheet, ReferenceSheetEvent, ReferenceSheetMarker } from "../../domain/plan/types";
import { ApiError } from "../../shared/api/http";
import { dayjs, type Dayjs } from "../../shared/lib/datetime";
import { activeDates, isSelectableDate } from "./markerForm";

interface Props {
  open: boolean;
  sheet: ReferenceSheet;
  marker: ReferenceSheetMarker;
  /** 動かす日付。null なら日付を追加 */
  event: ReferenceSheetEvent | null;
  today: string;
  onClose: () => void;
  onSaved: () => void;
}

interface Values {
  date?: Dayjs;
  dates?: Dayjs[];
}

const fmt = (d: string) => dayjs(d, "YYYY-MM-DD").format("YYYY/M/D(dd)");
const CASCADE_NOTE = "（このマーカーを参照するテンプレートの未来の予定も更新しました）";

/**
 * マーカーの日付を追加する・1件動かす（例: 振込日を 11/25 → 11/26 に）。
 * 選べるのは今日以降でシートの期間内の日付のみ（過去は実績のため変更しない）。
 */
export function EventDateModal({ open, sheet, marker, event, today, onClose, onSaved }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<Values>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // このマーカーに既にある日付は選べない（動かす先にできず、追加しても無視される）。取り消した日付は追加し直せる（取消が解除される）
  const taken = new Set(activeDates(marker));
  const disabledDate = (d: Dayjs) => !isSelectableDate(d.format("YYYY-MM-DD"), sheet, today, taken);

  // 開くたびに初期値へ戻す（forceRender でフォームは閉じても残るため）
  useEffect(() => {
    if (open) {
      form.resetFields();
      setError(null);
    }
  }, [open, event, form]);

  const onFinish = async (v: Values) => {
    setSubmitting(true);
    setError(null);
    try {
      if (event) {
        const to = v.date!.format("YYYY-MM-DD");
        await sheetApi.moveEvent(event.id, to);
        message.success(`「${marker.marker}」を ${fmt(event.event_date)} から ${fmt(to)} に変更しました${CASCADE_NOTE}`);
      } else {
        const dates = (v.dates ?? []).map((d) => d.format("YYYY-MM-DD")).sort();
        await sheetApi.addEvents(sheet.id, marker.marker, dates);
        message.success(`「${marker.marker}」に ${dates.length} 日を追加しました${CASCADE_NOTE}`);
      }
      form.resetFields();
      onClose();
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "日付を保存できませんでした。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title={event ? `「${marker.marker}」の日付を変更` : `「${marker.marker}」に日付を追加`}
      okText={event ? "変更する" : "追加する"}
      cancelText="キャンセル"
      onOk={() => form.submit()}
      onCancel={onClose}
      confirmLoading={submitting}
      forceRender
    >
      <Typography.Paragraph type="secondary" className="modal-note">
        {event ? `現在の日付: ${fmt(event.event_date)}。` : ""}
        選べるのは今日以降、シートの期間（{sheet.period_start}〜{sheet.period_end}）内の日付です。
        保存すると、このマーカーを参照するテンプレートの未来の予定が作り直されます。
        {marker.is_generated && "人が決めた日付として記録され、ルールを変えても上書きされません。"}
      </Typography.Paragraph>

      {error && <Alert type="error" showIcon message="保存できませんでした" description={error} className="modal-alert" />}

      <Form form={form} layout="vertical" onFinish={onFinish} requiredMark="optional" disabled={submitting}>
        {event ? (
          <Form.Item name="date" label="新しい日付" rules={[{ required: true, message: "日付を選択してください" }]}>
            <DatePicker disabledDate={disabledDate} defaultPickerValue={dayjs(event.event_date, "YYYY-MM-DD")} />
          </Form.Item>
        ) : (
          <Form.Item
            name="dates"
            label="追加する日付"
            extra="複数選択できます。"
            rules={[{ required: true, type: "array", min: 1, message: "日付を選択してください" }]}
          >
            <DatePicker multiple disabledDate={disabledDate} maxTagCount="responsive" style={{ width: "100%" }} />
          </Form.Item>
        )}
      </Form>
    </Modal>
  );
}
