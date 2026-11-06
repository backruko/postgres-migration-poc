import { Alert, App, Form, Input, Modal, Typography } from "antd";
import { useEffect, useState } from "react";
import { staffApi } from "../../domain/staff/api";
import { newPasswordProblem } from "../../domain/staff/lib/login";
import type { Staff } from "../../domain/staff/types";
import { ApiError } from "../../shared/api/http";

interface Props {
  open: boolean;
  staff: Staff | null;
  onClose: () => void;
  onSaved: () => void;
}

/**
 * 仮パスワードの設定（システム管理者。新しい職員・パスワードを忘れたとき・ロックされたとき）。
 * 本人は次のログインで自分のパスワードに変えるまで、ほかの操作はできない。開いているセッションは終わる。
 */
export function TemporaryPasswordModal({ open, staff, onClose, onSaved }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<{ password?: string }>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      form.resetFields();
      setError(null);
    }
  }, [open, form]);

  const onFinish = async (v: { password?: string }) => {
    if (!staff) return;
    setSubmitting(true);
    setError(null);
    try {
      await staffApi.setTemporaryPassword(staff.id, v.password ?? "");
      message.success(`「${staff.name}」の仮パスワードを設定しました。本人に伝えてください（次のログインで変更します）。`, 6);
      onClose();
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "設定できませんでした。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal open={open} title={staff ? `仮パスワードを設定（${staff.name}）` : "仮パスワードを設定"} okText="設定" cancelText="キャンセル"
      confirmLoading={submitting} onOk={() => form.submit()} onCancel={onClose} forceRender>
      <Typography.Paragraph type="secondary">
        本人は次のログインでこの仮パスワードを使い、自分のパスワードに変えます。ロックは解け、開いているログインは終わります。
      </Typography.Paragraph>
      <Form form={form} layout="vertical" onFinish={onFinish} requiredMark={false}>
        <Form.Item
          name="password"
          label="仮パスワード"
          extra="8 文字以上。本人には口頭・別の手段で伝えてください。"
          rules={[{ required: true, message: "入力してください" }, { validator: (_, value: string | undefined) => {
            const problem = newPasswordProblem(value ?? "", staff?.login_id);
            return problem ? Promise.reject(new Error(problem)) : Promise.resolve();
          } }]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
      </Form>
      {error && <Alert type="error" showIcon message={error} />}
    </Modal>
  );
}
