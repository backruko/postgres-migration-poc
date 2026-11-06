import { Alert, App, Button, Form, Input, Space } from "antd";
import { useState } from "react";
import { authApi } from "../../domain/staff/api";
import { newPasswordProblem, passwordChangeErrorText } from "../../domain/staff/lib/login";
import type { Staff } from "../../domain/staff/types";

interface Values {
  current_password?: string;
  new_password?: string;
  confirm?: string;
}

interface Props {
  me: Staff;
  /** 変更できたとき */
  onChanged: (me: Staff) => void;
  onCancel?: () => void;
}

/** 本人のパスワードの変更（仮パスワードからの変更にも使う。このセッションは続き、ほかのセッションは終わる） */
export function PasswordChangeForm({ me, onChanged, onCancel }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<Values>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onFinish = async (v: Values) => {
    setSubmitting(true);
    setError(null);
    try {
      const updated = await authApi.changePassword({ current_password: v.current_password ?? "", new_password: v.new_password ?? "" });
      message.success("パスワードを変更しました。ほかの端末のログインは終わりました。");
      form.resetFields();
      onChanged(updated);
    } catch (e) {
      setError(passwordChangeErrorText(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Form<Values> form={form} layout="vertical" onFinish={onFinish} requiredMark={false} disabled={submitting}>
      <Form.Item name="current_password" label={me.must_change_password ? "仮パスワード" : "今のパスワード"} rules={[{ required: true, message: "入力してください" }]}>
        <Input.Password autoComplete="current-password" autoFocus />
      </Form.Item>
      <Form.Item
        name="new_password"
        label="新しいパスワード"
        extra="8 文字以上。ログインIDと同じものは使えません。"
        rules={[{ required: true, message: "入力してください" }, { validator: (_, value: string | undefined) => {
          const problem = newPasswordProblem(value ?? "", me.login_id);
          return problem ? Promise.reject(new Error(problem)) : Promise.resolve();
        } }]}
      >
        <Input.Password autoComplete="new-password" />
      </Form.Item>
      <Form.Item
        name="confirm"
        label="新しいパスワード（確認）"
        dependencies={["new_password"]}
        rules={[{ required: true, message: "入力してください" }, ({ getFieldValue }) => ({
          validator: (_, value: string | undefined) =>
            !value || value === getFieldValue("new_password") ? Promise.resolve() : Promise.reject(new Error("新しいパスワードと一致しません")),
        })]}
      >
        <Input.Password autoComplete="new-password" />
      </Form.Item>
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
      <Space>
        <Button type="primary" htmlType="submit" loading={submitting}>パスワードを変更</Button>
        {onCancel && <Button onClick={onCancel}>キャンセル</Button>}
      </Space>
    </Form>
  );
}
