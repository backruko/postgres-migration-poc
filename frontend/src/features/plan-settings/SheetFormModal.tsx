import { Alert, App, DatePicker, Form, Input, Modal, Typography } from "antd";
import { useEffect, useState } from "react";
import { sheetApi } from "../../domain/plan/api";
import type { ReferenceSheet } from "../../domain/plan/types";
import { ApiError } from "../../shared/api/http";
import { TIMEZONE } from "../../shared/config";
import { dayjs, todayIn, type Dayjs } from "../../shared/lib/datetime";
import { defaultSheetPeriod } from "./markerForm";

interface Values {
  name: string;
  period: [Dayjs, Dayjs];
}

interface Props {
  open: boolean;
  clientCode: string;
  /** 編集するシート。null なら新しいシートを作る */
  sheet: ReferenceSheet | null;
  /** 同じ顧客のシート（同じ名前を先に知らせる） */
  sheets: ReferenceSheet[];
  onClose: () => void;
  onSaved: (sheet: ReferenceSheet) => void;
}

const ymd = (d: Dayjs) => d.format("YYYY-MM-DD");

/**
 * 年間計画シートを作る（名前と期間だけの空のシート。マーカーと日付はあとから足す）・名前と期間を変える（E.3）。
 * 期間はいつでも延ばせる（ルールのマーカーは新しい期間まで日付を作る）。縮めるのは、外に残る日付が無いときだけ（サーバーが判定）。
 */
export function SheetFormModal({ open, clientCode, sheet, sheets, onClose, onSaved }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<Values>();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isEdit = sheet !== null;

  useEffect(() => {
    if (!open) return;
    form.resetFields();
    const period = sheet
      ? { start: sheet.period_start, end: sheet.period_end }
      : defaultSheetPeriod(todayIn(TIMEZONE));
    form.setFieldsValue({
      name: sheet?.name ?? "",
      period: [dayjs(period.start, "YYYY-MM-DD"), dayjs(period.end, "YYYY-MM-DD")],
    });
    setError(null);
    setSaving(false);
  }, [open, sheet, form]);

  const save = async (v: Values) => {
    setSaving(true);
    setError(null);
    const name = v.name.trim();
    const [start, end] = v.period.map(ymd);
    try {
      let saved: ReferenceSheet;
      if (sheet) {
        const body = {
          ...(name !== sheet.name ? { name } : {}),
          ...(start !== sheet.period_start ? { period_start: start } : {}),
          ...(end !== sheet.period_end ? { period_end: end } : {}),
        };
        if (!Object.keys(body).length) {
          onClose();
          return;
        }
        saved = await sheetApi.updateSheet(sheet.id, body);
        message.success(`シート「${saved.name}」を保存しました`);
      } else {
        saved = await sheetApi.createSheet({ client_code: clientCode, name, period_start: start, period_end: end });
        message.success(`シート「${saved.name}」を作りました。マーカーを追加してください`);
      }
      onClose();
      onSaved(saved);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "保存できませんでした。");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={sheet ? `シート「${sheet.name}」を編集` : "新しいシート"}
      okText={isEdit ? "保存する" : "作る"}
      cancelText="キャンセル"
      onOk={() => form.submit()}
      onCancel={onClose}
      confirmLoading={saving}
      forceRender
    >
      <Typography.Paragraph type="secondary" className="modal-note">
        顧客 {clientCode}。
        {isEdit
          ? "期間はいつでも延ばせます（ルールで日付を作るマーカーは、新しい期間まで日付を作ります）。縮めるのは、新しい期間の外に日付が残らないときだけです。"
          : "名前と期間だけの空のシートを作ります。マーカーと日付は、作ったあとに追加します（JSON ファイルの取り込みも使えます）。"}
      </Typography.Paragraph>
      {error && <Alert type="error" showIcon message="保存できませんでした" description={error} className="modal-alert" />}
      <Form form={form} layout="vertical" onFinish={save} disabled={saving} requiredMark="optional">
        <Form.Item
          name="name"
          label="シートの名前"
          rules={[
            { required: true, whitespace: true, message: "名前を入力してください" },
            { max: 100, message: "100文字以内で入力してください" },
            {
              validator: (_, value?: string) =>
                !value || !sheets.some((s) => s.id !== sheet?.id && s.name === value.trim())
                  ? Promise.resolve()
                  : Promise.reject(new Error("この顧客に同じ名前のシートがあります")),
            },
          ]}
        >
          <Input maxLength={100} placeholder="例: 2027年 年間計画" />
        </Form.Item>
        <Form.Item
          name="period"
          label="期間"
          rules={[
            { required: true, message: "期間を選んでください" },
            {
              validator: (_, value?: [Dayjs, Dayjs]) =>
                !value || value[1].isAfter(value[0], "day") ? Promise.resolve() : Promise.reject(new Error("終わりは始まりより後にしてください")),
            },
          ]}
        >
          <DatePicker.RangePicker allowClear={false} />
        </Form.Item>
      </Form>
    </Modal>
  );
}
