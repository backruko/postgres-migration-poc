import { Alert, App, Checkbox, Form, Input, InputNumber, Modal, Radio, Select, Switch, TimePicker, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { useBizMasters } from "../../domain/biz/hooks/useBizMasters";
import { useClients } from "../../domain/client/hooks/useClients";
import { notificationSettingsApi } from "../../domain/notifications/api/settingsApi";
import {
  QUIET_MODE_LABEL, type RuleForm, formToRule, ruleToForm, selectableDestinations, settingsErrorText,
} from "../../domain/notifications/lib/settings";
import type { NotificationDestination, NotificationOptions, NotificationRule } from "../../domain/notifications/types";
import type { Staff } from "../../domain/staff/types";
import { ApiError } from "../../shared/api/http";
import { dayjs, type Dayjs } from "../../shared/lib/datetime";

interface Props {
  open: boolean;
  /** 直すルール。null なら新しく作る */
  rule: NotificationRule | null;
  me: Staff;
  options: NotificationOptions | null;
  destinations: NotificationDestination[];
  onClose: () => void;
  onSaved: () => void;
}

const ALL = "__all__";
type Values = Omit<RuleForm, "quiet_start" | "quiet_end" | "client_code"> & { client: string; quiet_range?: [Dayjs, Dayjs] };

/**
 * ルールを作る・直す。どの出来事を・どの顧客（業務）について・どの宛先に・どの文面で知らせるか、
 * 重複の期間と知らせない時間帯。変えた後に処理する出来事から効く（作った通知はそのまま）。
 */
export function RuleFormModal({ open, rule, me, options, destinations, onClose, onSaved }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<Values>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const clients = useClients();
  const bizes = useBizMasters();
  const limited = me.client_codes.length > 0;
  const client = Form.useWatch("client", form);
  const quiet = Form.useWatch("quiet", form);
  const ruleClient = client && client !== ALL ? client : null;

  useEffect(() => {
    if (open) {
      const f = ruleToForm(rule, { client: limited ? me.client_codes[0] : null, template: options?.templates[0]?.code ?? "order_deadline" });
      form.resetFields();
      form.setFieldsValue({
        ...f,
        client: f.client_code ?? ALL,
        quiet_range: f.quiet_start && f.quiet_end ? [dayjs(f.quiet_start, "HH:mm"), dayjs(f.quiet_end, "HH:mm")] : undefined,
      });
      setError(null);
    }
  }, [open, rule, form, limited, me.client_codes, options]);

  const destinationOptions = useMemo(
    () => selectableDestinations(destinations, ruleClient).map((d) => ({
      value: d.id,
      label: `${d.name}（${d.client_code ?? "全顧客"}${d.enabled ? "" : "・無効"}）`,
    })),
    [destinations, ruleClient],
  );
  const bizOptions = (bizes.data ?? [])
    .filter((b) => ruleClient !== null && b.client_code === ruleClient)
    .map((b) => ({ value: b.id, label: `${b.business_code} ${b.name}` }));

  const onFinish = async (v: Values) => {
    setSubmitting(true);
    setError(null);
    const body = formToRule({
      ...v,
      client_code: v.client === ALL ? null : v.client,
      quiet_start: v.quiet && v.quiet_range ? v.quiet_range[0].format("HH:mm") : null,
      quiet_end: v.quiet && v.quiet_range ? v.quiet_range[1].format("HH:mm") : null,
    });
    try {
      if (rule) {
        await notificationSettingsApi.updateRule(rule.id, body);
        message.success(`ルール「${body.name}」を保存しました。次に起きる出来事から効きます。`);
      } else {
        await notificationSettingsApi.createRule(body);
        message.success(`ルール「${body.name}」を作りました。`);
      }
      onClose();
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? settingsErrorText(e, "rule") : "保存できませんでした。");
    } finally {
      setSubmitting(false);
    }
  };

  const clientOptions = [
    ...(limited ? [] : [{ value: ALL, label: "全顧客" }]),
    ...(clients.data ?? []).map((c) => ({ value: c.code, label: `${c.code} ${c.name}` })),
  ];

  return (
    <Modal
      open={open}
      title={rule ? `ルールを直す（${rule.name}）` : "ルールを作る"}
      okText={rule ? "保存" : "作る"}
      cancelText="キャンセル"
      confirmLoading={submitting}
      onOk={() => form.submit()}
      onCancel={onClose}
      width={620}
      forceRender
    >
      <Form form={form} layout="vertical" onFinish={onFinish} requiredMark={false}
        onValuesChange={(changed) => {
          // 顧客を変えたら、その顧客で使えない業務・宛先を外す
          if ("client" in changed) {
            const next = changed.client === ALL ? null : (changed.client as string);
            const usable = new Set(selectableDestinations(destinations, next).map((d) => d.id));
            form.setFieldsValue({ biz_master_id: null, destination_ids: (form.getFieldValue("destination_ids") as number[]).filter((id) => usable.has(id)) });
          }
        }}
      >
        <Form.Item name="name" label="名前" rules={[{ required: true, whitespace: true, message: "名前を入力してください" }, { max: 100 }]}>
          <Input maxLength={100} placeholder="例: MOCK の遅れを運用チームへ" autoComplete="off" />
        </Form.Item>
        <Form.Item name="event_types" label="知らせる出来事" rules={[{ required: true, type: "array", min: 1, message: "1 つ以上選んでください" }]}>
          <Checkbox.Group options={(options?.event_types ?? []).map((e) => ({ value: e.code, label: e.label }))} />
        </Form.Item>
        <Form.Item name="client" label="顧客" extra={limited ? "担当の顧客のルールだけを作れます。" : "全顧客のルールは、すべての顧客の出来事に反応します。"}>
          <Select options={clientOptions} loading={clients.loading} showSearch optionFilterProp="label" />
        </Form.Item>
        <Form.Item name="biz_master_id" label="業務（任意）" extra={ruleClient ? "空なら、その顧客のすべての業務。" : "顧客を選ぶと、業務で絞れます。"}>
          <Select allowClear disabled={!ruleClient} options={bizOptions} placeholder="すべての業務" showSearch optionFilterProp="label" />
        </Form.Item>
        <Form.Item name="destination_ids" label="宛先" rules={[{ required: true, type: "array", min: 1, message: "1 つ以上選んでください" }]}
          extra={ruleClient ? "全顧客の宛先と、この顧客の宛先を選べます。" : "顧客の宛先を選ぶと、その顧客の出来事のときだけ届きます。"}>
          <Select mode="multiple" options={destinationOptions} placeholder="宛先を選ぶ" optionFilterProp="label" />
        </Form.Item>
        <Form.Item name="template_code" label="文面">
          <Select options={(options?.templates ?? []).map((t) => ({ value: t.code, label: t.name }))} />
        </Form.Item>
        <Form.Item name="dedupe_window_minutes" label="重複の期間（分。任意）"
          extra="同じオーダー・同じ種類の通知を、この分数のうちは 2 回出しません（締切が何度も動いたとき）。「まもなく」が「遅れ」を消すことはありません。">
          <InputNumber min={1} max={10080} precision={0} placeholder="なし" style={{ width: 140 }} />
        </Form.Item>
        <Form.Item name="quiet" label="知らせない時間帯" valuePropName="checked">
          <Switch />
        </Form.Item>
        {quiet && (
          <>
            <Form.Item name="quiet_range" label="時間帯（日本時間。日をまたいでよい）" rules={[{ required: true, message: "時間帯を選んでください" }]}>
              <TimePicker.RangePicker format="HH:mm" minuteStep={5} order={false} />
            </Form.Item>
            <Form.Item name="quiet_mode" label="その時間帯の出来事">
              <Radio.Group options={(Object.keys(QUIET_MODE_LABEL) as (keyof typeof QUIET_MODE_LABEL)[]).map((k) => ({ value: k, label: QUIET_MODE_LABEL[k] }))} />
            </Form.Item>
          </>
        )}
        <Form.Item name="enabled" label="有効" valuePropName="checked">
          <Switch />
        </Form.Item>
      </Form>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
        変更は、保存した後に起きる出来事から効きます。もう作った通知は、そのときのルールのままです。
      </Typography.Paragraph>
      {error && <Alert type="error" showIcon message={error} style={{ marginTop: 12 }} />}
    </Modal>
  );
}
