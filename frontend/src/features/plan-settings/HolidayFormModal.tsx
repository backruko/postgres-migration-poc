import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import { Alert, App, Button, DatePicker, Form, Input, Modal, Space, Typography } from "antd";
import { useEffect, useState } from "react";
import { calendarApi } from "../../domain/plan/api";
import type { BusinessCalendar, Holiday } from "../../domain/plan/types";
import { ApiError } from "../../shared/api/http";
import { dayjs, type Dayjs } from "../../shared/lib/datetime";
import { describeChange, isEditableHoliday, parseHolidayLines, toHolidayInputs, type HolidayRow } from "./holidayForm";

interface Props {
  open: boolean;
  calendar: BusinessCalendar;
  /** 名前を変更する休日。null なら休日を追加 */
  holiday: Holiday | null;
  /** 登録済みの休日（追加では選べない） */
  existing: ReadonlySet<string>;
  today: string;
  onClose: () => void;
  onSaved: () => void;
}

interface Values {
  rows?: { date?: Dayjs; name?: string }[];
  name?: string;
  paste?: string;
}

const fmt = (d: string) => dayjs(d, "YYYY-MM-DD").format("YYYY/M/D(dd)");

/**
 * 休日の追加（複数行・一覧の貼り付け）と、名前の変更。
 * 追加できるのは今日以降の日付のみ（過去は実績のため変えない）。保存すると、このカレンダーを使うテンプレートと
 * 生成マーカーの今日以降の日付が作り直される（名前の変更だけなら作り直さない）。
 */
export function HolidayFormModal({ open, calendar, holiday, existing, today, onClose, onSaved }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<Values>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pasteNote, setPasteNote] = useState<string | null>(null);

  // 開くたびに初期値へ戻す（forceRender でフォームは閉じても残るため）
  useEffect(() => {
    if (open) {
      form.resetFields();
      form.setFieldsValue(holiday ? { name: holiday.name ?? "" } : { rows: [{}] });
      setError(null);
      setPasteNote(null);
    }
  }, [open, holiday, form]);

  const disabledDate = (d: Dayjs) => {
    const s = d.format("YYYY-MM-DD");
    return !isEditableHoliday(s, today) || existing.has(s);
  };

  // 貼り付けた一覧を行に読み込む（今日より前・登録済み・入力済みの日付は飛ばす）
  const loadPasted = () => {
    const { rows, errors } = parseHolidayLines(form.getFieldValue("paste") ?? "");
    const current = ((form.getFieldValue("rows") ?? []) as NonNullable<Values["rows"]>).filter((r) => r?.date);
    const taken = new Set(current.map((r) => r.date!.format("YYYY-MM-DD")));
    const skipped: string[] = [];
    const added: HolidayRow[] = [];
    for (const r of rows) {
      if (!isEditableHoliday(r.date, today) || existing.has(r.date) || taken.has(r.date)) {
        skipped.push(r.date);
        continue;
      }
      taken.add(r.date);
      added.push(r);
    }
    form.setFieldsValue({
      rows: [...current, ...added.map((r) => ({ date: dayjs(r.date, "YYYY-MM-DD"), name: r.name }))],
      paste: "",
    });
    setPasteNote(
      [
        `${added.length} 件を読み込みました。`,
        skipped.length ? `今日より前・登録済み・重複の ${skipped.length} 件は飛ばしました（${skipped.map(fmt).join("、")}）。` : "",
        errors.length ? `日付として読めない行: ${errors.map((e) => `${e.line}行目「${e.text}」`).join("、")}` : "",
      ]
        .filter(Boolean)
        .join(" "),
    );
  };

  const onFinish = async (v: Values) => {
    setSubmitting(true);
    setError(null);
    try {
      const inputs = holiday
        ? toHolidayInputs([{ date: holiday.holiday_date, name: v.name }])
        : toHolidayInputs((v.rows ?? []).filter((r) => r.date).map((r) => ({ date: r.date!.format("YYYY-MM-DD"), name: r.name })));
      const result = await calendarApi.addHolidays(calendar.id, inputs);
      message.success(describeChange(result), 6);
      form.resetFields();
      onClose();
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "休日を保存できませんでした。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title={holiday ? `${fmt(holiday.holiday_date)} の名前を変更` : `「${calendar.name}」に休日を追加`}
      okText={holiday ? "保存する" : "追加する"}
      cancelText="キャンセル"
      onOk={() => form.submit()}
      onCancel={onClose}
      confirmLoading={submitting}
      width={holiday ? 480 : 620}
      forceRender
    >
      <Typography.Paragraph type="secondary" className="modal-note">
        {holiday
          ? "名前だけを変更します（予定は作り直されません）。"
          : "追加できるのは今日以降の日付です。追加すると、このカレンダーを使うテンプレートと、ルールで日付を作るマーカーの今日以降の日付が休日の扱いに従って作り直されます（人が変更した日付はそのまま）。"}
      </Typography.Paragraph>

      {error && <Alert type="error" showIcon message="保存できませんでした" description={error} className="modal-alert" />}

      <Form form={form} layout="vertical" onFinish={onFinish} requiredMark="optional" disabled={submitting}>
        {holiday ? (
          <Form.Item name="name" label="名前" rules={[{ max: 100, message: "100文字以内で入力してください" }]}>
            <Input maxLength={100} placeholder="例: 元日" />
          </Form.Item>
        ) : (
          <>
            <Form.List
              name="rows"
              rules={[
                {
                  validator: async (_, rows?: Values["rows"]) => {
                    if (!rows?.some((r) => r?.date)) throw new Error("休日を1件以上入力してください");
                  },
                },
              ]}
            >
              {(fields, { add, remove }, { errors }) => (
                <>
                  {fields.map((field) => (
                    <Space key={field.key} align="start" className="holiday-row">
                      <Form.Item
                        name={[field.name, "date"]}
                        rules={[
                          { required: true, message: "日付を選択してください" },
                          ({ getFieldValue }) => ({
                            validator: async (_, d?: Dayjs) => {
                              if (!d) return;
                              const s = d.format("YYYY-MM-DD");
                              const same = (getFieldValue("rows") as Values["rows"] ?? []).filter((r) => r?.date?.format("YYYY-MM-DD") === s);
                              if (same.length > 1) throw new Error("同じ日付が複数あります");
                            },
                          }),
                        ]}
                      >
                        <DatePicker disabledDate={disabledDate} placeholder="日付" />
                      </Form.Item>
                      <Form.Item name={[field.name, "name"]} rules={[{ max: 100, message: "100文字以内" }]}>
                        <Input maxLength={100} placeholder="名前（例: 元日）" style={{ width: 240 }} />
                      </Form.Item>
                      <Button
                        type="text"
                        icon={<DeleteOutlined />}
                        aria-label="この行を削除"
                        onClick={() => remove(field.name)}
                        disabled={fields.length === 1}
                      />
                    </Space>
                  ))}
                  <Button type="dashed" icon={<PlusOutlined />} onClick={() => add({})}>
                    行を追加
                  </Button>
                  <Form.ErrorList errors={errors} />
                </>
              )}
            </Form.List>

            <Form.Item
              name="paste"
              label="一覧を貼り付けて読み込む（任意）"
              extra="1行に「日付 名前」。例: 2027-01-01 元日 / 2027/1/11 成人の日"
              className="holiday-paste"
            >
              <Input.TextArea rows={4} placeholder={"2027-01-01 元日\n2027-01-11 成人の日"} />
            </Form.Item>
            <Button onClick={loadPasted}>行に読み込む</Button>
            {pasteNote && <Alert type="info" showIcon message={pasteNote} className="modal-alert" />}
          </>
        )}
      </Form>
    </Modal>
  );
}
