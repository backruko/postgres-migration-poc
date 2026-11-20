import { Alert, App, Form, Input, Modal, Select, Switch, Typography } from "antd";
import { useEffect, useState } from "react";
import { useClients } from "../../domain/client/hooks/useClients";
import { notificationSettingsApi } from "../../domain/notifications/api/settingsApi";
import {
  type DestinationForm, destinationToForm, formToDestination, settingsErrorText,
} from "../../domain/notifications/lib/settings";
import type { NotificationDestination } from "../../domain/notifications/types";
import type { Staff } from "../../domain/staff/types";
import { ApiError } from "../../shared/api/http";

interface Props {
  open: boolean;
  /** 直す宛先。null なら新しく作る */
  destination: NotificationDestination | null;
  me: Staff;
  onClose: () => void;
  onSaved: () => void;
}

/** 選択肢の「全顧客」の値（Select は null を値にできないため） */
const ALL = "__all__";

/**
 * 宛先を作る・直す。届け方は今はメールだけ。担当顧客が限られた業務管理者は、自分の顧客の宛先だけ
 * （「全顧客」は選べない）。アドレスを直すと、まだ再試行中の配信も新しいアドレスへ送る。
 */
export function DestinationFormModal({ open, destination, me, onClose, onSaved }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<DestinationForm & { client: string }>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const clients = useClients();
  const limited = me.client_codes.length > 0;

  useEffect(() => {
    if (open) {
      const initial = destinationToForm(destination, limited ? me.client_codes[0] : null);
      form.resetFields();
      form.setFieldsValue({ ...initial, client: initial.client_code ?? ALL });
      setError(null);
    }
  }, [open, destination, form, limited, me.client_codes]);

  const onFinish = async (v: DestinationForm & { client: string }) => {
    setSubmitting(true);
    setError(null);
    const body = formToDestination({ ...v, client_code: v.client === ALL ? null : v.client });
    try {
      if (destination) {
        await notificationSettingsApi.updateDestination(destination.id, body);
        message.success(`宛先「${body.name}」を保存しました。`);
      } else {
        await notificationSettingsApi.createDestination(body);
        message.success(`宛先「${body.name}」を作りました。`);
      }
      onClose();
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? settingsErrorText(e, "destination") : "保存できませんでした。");
    } finally {
      setSubmitting(false);
    }
  };

  const clientOptions = [
    ...(limited ? [] : [{ value: ALL, label: "全顧客（どの顧客の通知にも使える）" }]),
    ...(clients.data ?? []).map((c) => ({ value: c.code, label: `${c.code} ${c.name}` })),
  ];

  return (
    <Modal
      open={open}
      title={destination ? `宛先を直す（${destination.name}）` : "宛先を作る"}
      okText={destination ? "保存" : "作る"}
      cancelText="キャンセル"
      confirmLoading={submitting}
      onOk={() => form.submit()}
      onCancel={onClose}
      forceRender
    >
      <Form form={form} layout="vertical" onFinish={onFinish} requiredMark={false}>
        <Form.Item name="name" label="名前" rules={[{ required: true, whitespace: true, message: "名前を入力してください" }, { max: 100 }]}>
          <Input maxLength={100} placeholder="例: 運用チームのメールボックス" autoComplete="off" />
        </Form.Item>
        <Form.Item
          name="address"
          label="メールアドレス"
          extra={destination ? "直すと、まだ送り直している配信も新しいアドレスへ送ります（記録には、それぞれの回に送ったアドレスが残ります）。" : undefined}
          rules={[{ required: true, whitespace: true, message: "メールアドレスを入力してください" }, { type: "email", message: "メールアドレスの形ではありません" }]}
        >
          <Input maxLength={2000} placeholder="ops@example.com" autoComplete="off" />
        </Form.Item>
        <Form.Item name="client" label="顧客" extra={limited ? "担当の顧客の宛先だけを作れます。" : "全顧客の宛先は、どの顧客のルールでも使えます。"}>
          <Select options={clientOptions} loading={clients.loading} showSearch optionFilterProp="label" />
        </Form.Item>
        <Form.Item name="owner" label="持ち主のチーム（任意）">
          <Input maxLength={100} autoComplete="off" />
        </Form.Item>
        <Form.Item name="enabled" label="有効" valuePropName="checked" extra="無効にすると、この宛先には送りません（ルールからは外れません）。">
          <Switch />
        </Form.Item>
      </Form>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
        作ったら一覧の「テスト送信」で、本物のアラームと同じ道で届くか確かめられます。
      </Typography.Paragraph>
      {error && <Alert type="error" showIcon message={error} style={{ marginTop: 12 }} />}
    </Modal>
  );
}
