import { Alert, App, AutoComplete, DatePicker, Divider, Form, Input, Modal, Select, Space, Typography } from "antd";
import { useEffect, useState } from "react";
import { sheetApi } from "../../domain/plan/api";
import { useBusinessCalendars } from "../../domain/plan/hooks/useClientCalendar";
import { describeRrule, POLICY_LABEL } from "../../domain/plan/lib/describe";
import type { HolidayPolicy, ReferenceSheet, ReferenceSheetMarker } from "../../domain/plan/types";
import { ApiError } from "../../shared/api/http";
import { TIMEZONE } from "../../shared/config";
import { dayjs, todayIn } from "../../shared/lib/datetime";
import {
  activeDates,
  fromMarker,
  futureManualDates,
  initialMarkerValues,
  MARKER_RRULE_PRESETS,
  ruleChanged,
  toCreateMarkerInput,
  toUpdateMarkerInput,
  type MarkerFormValues,
} from "./markerForm";
import { rruleRule } from "./templateForm";

interface Props {
  open: boolean;
  sheet: ReferenceSheet;
  /** 編集するマーカー。null なら生成マーカーを新規作成 */
  marker: ReferenceSheetMarker | null;
  onClose: () => void;
  /** 作成・保存したとき（一覧の再読み込み用） */
  onSaved: () => void;
}

/**
 * 生成マーカー（ルールから基準日を作るマーカー。例: 振込日 = 毎月27日、休日なら翌営業日）の作成・編集。
 * 取り込んだマーカーは説明だけを編集できる。マーカー名は作成後に変更できない（テンプレートが名前で参照するため）。
 */
export function MarkerFormModal({ open, sheet, marker, onClose, onSaved }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<MarkerFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isEdit = marker !== null;
  const withRule = !marker || marker.is_generated;

  const rrule = Form.useWatch("rrule", form);
  const holidayPolicy = Form.useWatch("holiday_policy", form);
  const values = Form.useWatch([], form) as Partial<MarkerFormValues> | undefined;
  const calendars = useBusinessCalendars();

  // 人が編集した未来の日付は、ルールを変えても残る。新しいルールの日付と重なると参照するジョブが二重に動くため、先に知らせる
  const keptDates = marker?.is_generated ? futureManualDates(marker, todayIn(TIMEZONE)) : [];
  const warnKept = marker !== null && keptDates.length > 0 && values !== undefined && ruleChanged(values, marker);
  const keptLabel = keptDates.map((d) => dayjs(d, "YYYY-MM-DD").format("YYYY/M/D(dd)")).join("、");

  // 開くたびに、新規なら初期値・編集ならそのマーカーの値へ戻す（forceRender でフォームは閉じても残るため）
  useEffect(() => {
    if (open) {
      form.resetFields();
      form.setFieldsValue(marker ? fromMarker(marker) : initialMarkerValues(sheet));
      setError(null);
    }
  }, [open, marker, sheet, form]);

  const onFinish = async (v: MarkerFormValues) => {
    setSubmitting(true);
    setError(null);
    try {
      if (marker) {
        const saved = await sheetApi.updateMarker(sheet.id, marker.marker, toUpdateMarkerInput(v, marker));
        const before = activeDates(marker).join();
        const moved = saved.is_generated && activeDates(saved).join() !== before;
        message.success(`「${saved.marker}」を保存しました${moved ? "（日付を作り直し、参照しているテンプレートの未来の予定も更新しました）" : ""}`);
        if (moved && keptDates.length) {
          message.warning(`人が編集した未来の日付（${keptLabel}）はそのまま残っています。新しいルールの日付と重なっていないか確認してください。`, 8);
        }
      } else {
        const input = toCreateMarkerInput(v);
        const savedSheet = await sheetApi.addMarker(sheet.id, input);
        const created = savedSheet.markers.find((m) => m.marker === input.marker);
        message.success(`「${input.marker}」を追加しました${created ? `（${activeDates(created).length} 日を生成）` : ""}`);
      }
      form.resetFields();
      onClose();
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : `マーカーを${isEdit ? "保存" : "追加"}できませんでした。`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title={marker ? `マーカー「${marker.marker}」を編集` : "ルールで日付を作るマーカーを追加"}
      okText={isEdit ? "保存する" : "追加する"}
      cancelText="キャンセル"
      onOk={() => form.submit()}
      onCancel={onClose}
      confirmLoading={submitting}
      width={600}
      forceRender
    >
      <Typography.Paragraph type="secondary" className="modal-note">
        シート: {sheet.name}（{sheet.period_start}〜{sheet.period_end}、顧客 {sheet.client_code}）
        {withRule && (
          <>
            <br />
            ルールの日付はシートの期間全体（過去の月も含む）に作られます。人が編集・取消した日付は、ルールを変えてもそのまま残ります。
            {isEdit && "ルールを変えて日付が動くと、このマーカーを参照するテンプレートの未来の予定が作り直されます。"}
          </>
        )}
      </Typography.Paragraph>

      {error && (
        <Alert type="error" showIcon message={isEdit ? "保存できませんでした" : "追加できませんでした"} description={error} className="modal-alert" />
      )}

      {warnKept && (
        <Alert
          type="warning"
          showIcon
          className="modal-alert"
          message="人が編集した未来の日付は、ルールを変えても残ります"
          description={`${keptLabel}。新しいルールの日付と同じ時期に重なると、このマーカーを参照するジョブが二重に作られることがあります。保存後に一覧の行を開いて確認し、不要な日付は削除してください（過去の日付は実績として残ります）。`}
        />
      )}

      <Form form={form} layout="vertical" onFinish={onFinish} requiredMark="optional" disabled={submitting}>
        <Space wrap align="start" size="large">
          <Form.Item
            name="marker"
            label="マーカー名"
            extra={isEdit ? "テンプレートが名前で参照するため変更できません" : "例: 振込日"}
            rules={[
              { required: true, whitespace: true, message: "マーカー名を入力してください" },
              { max: 50, message: "50文字以内で入力してください" },
              {
                validator: (_, value?: string) =>
                  isEdit || !value || !sheet.markers.some((m) => m.marker === value.trim())
                    ? Promise.resolve()
                    : Promise.reject(new Error("このシートに同じ名前のマーカーがあります")),
              },
            ]}
          >
            <Input disabled={isEdit} maxLength={50} style={{ width: 200 }} />
          </Form.Item>
          <Form.Item name="description" label="説明" rules={[{ max: 200, message: "200文字以内で入力してください" }]}>
            <Input maxLength={200} placeholder="例: 決済代行会社への振込日" style={{ width: 300 }} />
          </Form.Item>
        </Space>

        {withRule && (
          <>
            <Divider orientation="left" plain>日付のルール</Divider>
            <Space wrap align="start" size="large">
              <Form.Item
                name="rrule"
                label="繰り返しルール（RRULE）"
                extra={rrule ? `→ ${describeRrule(rrule)}` : "例: FREQ=MONTHLY;BYMONTHDAY=27"}
                rules={[{ required: true, whitespace: true, message: "RRULE を入力してください" }, rruleRule]}
              >
                <AutoComplete
                  options={MARKER_RRULE_PRESETS.map((r) => ({ value: r, label: `${describeRrule(r)}　${r}` }))}
                  style={{ width: 300 }}
                />
              </Form.Item>
              <Form.Item name="anchor_date" label="起点日" rules={[{ required: true, message: "起点日を選択してください" }]}>
                <DatePicker allowClear={false} />
              </Form.Item>
            </Space>
            <Space wrap align="start" size="large">
              <Form.Item name="holiday_policy" label="非営業日に当たった場合">
                <Select
                  style={{ width: 300 }}
                  options={(Object.keys(POLICY_LABEL) as HolidayPolicy[]).map((p) => ({ value: p, label: POLICY_LABEL[p] }))}
                />
              </Form.Item>
              <Form.Item
                name="business_calendar_id"
                label="営業日カレンダー"
                dependencies={["holiday_policy"]}
                rules={[
                  {
                    required: holidayPolicy !== undefined && holidayPolicy !== "as_is",
                    message: "休日の振替・スキップには、営業日カレンダーが必要です",
                  },
                ]}
              >
                <Select
                  allowClear
                  style={{ width: 220 }}
                  loading={calendars.loading}
                  placeholder={holidayPolicy && holidayPolicy !== "as_is" ? "選択してください" : "なし"}
                  options={(calendars.data ?? []).map((c) => ({ value: c.id, label: `${c.name}（${c.code}）` }))}
                />
              </Form.Item>
            </Space>
          </>
        )}
      </Form>
    </Modal>
  );
}
