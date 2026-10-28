import { Alert, App, Checkbox, Form, Input, Modal, Typography } from "antd";
import { useEffect, useState } from "react";
import { staffApi } from "../../domain/staff/api";
import type { Role, Staff } from "../../domain/staff/types";
import { ApiError } from "../../shared/api/http";
import { lockedRoles, rolesChanged, toCreateInput, toUpdateInput, type StaffFormValues } from "./staffForm";

interface Props {
  open: boolean;
  /** 編集する職員。null なら新規登録 */
  staff: Staff | null;
  me: Staff | null;
  roles: Role[];
  onClose: () => void;
  onSaved: () => void;
}

/**
 * 職員の登録と編集（氏名・メールアドレス・ロール）。
 * 登録した職員は「利用開始前」で作られ、一覧の「有効化」で使えるようになる。ログインIDは登録後に変えられない。
 */
export function StaffFormModal({ open, staff, me, roles, onClose, onSaved }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<StaffFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const locked = lockedRoles(staff, me);

  // 開くたびに初期値へ戻す（forceRender でフォームは閉じても残るため）
  useEffect(() => {
    if (open) {
      form.resetFields();
      form.setFieldsValue(staff ? { login_id: staff.login_id, name: staff.name, email: staff.email ?? "", roles: staff.roles } : { roles: [] });
      setError(null);
    }
  }, [open, staff, form]);

  const onFinish = async (v: StaffFormValues) => {
    setSubmitting(true);
    setError(null);
    try {
      if (staff === null) {
        const created = await staffApi.create(toCreateInput(v));
        message.success(`「${created.name}」を登録しました（利用開始前）。一覧の「有効化」で使えるようになります。`, 6);
      } else {
        const update = toUpdateInput(staff, v);
        if (update) await staffApi.update(staff.id, update);
        const nextRoles = v.roles ?? [];
        if (rolesChanged(staff, nextRoles)) await staffApi.setRoles(staff.id, nextRoles);
        message.success(update || rolesChanged(staff, nextRoles) ? `「${(v.name ?? "").trim()}」を保存しました。` : "変更はありませんでした。");
      }
      onClose();
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "保存できませんでした。");
      onSaved(); // 氏名だけ保存されてロールで失敗した場合も一覧を最新にする
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title={staff ? `「${staff.name}」を編集` : "職員を登録"}
      okText={staff ? "保存する" : "登録する"}
      cancelText="キャンセル"
      onOk={() => form.submit()}
      onCancel={onClose}
      confirmLoading={submitting}
      width={560}
      forceRender
    >
      {!staff && (
        <Typography.Paragraph type="secondary" className="modal-note">
          登録した職員は「利用開始前」になります。一覧の「有効化」で使えるようになります。
        </Typography.Paragraph>
      )}

      {error && <Alert type="error" showIcon message="保存できませんでした" description={error} className="modal-alert" />}

      <Form form={form} layout="vertical" onFinish={onFinish} requiredMark="optional" disabled={submitting}>
        <Form.Item
          name="login_id"
          label="ログインID"
          extra={staff ? "ログインIDは変更できません。" : "社員番号やメールアドレスなど、職員ごとに一意な値"}
          rules={[{ required: true, whitespace: true, message: "ログインIDを入力してください" }, { max: 100 }]}
        >
          <Input maxLength={100} disabled={staff !== null} autoComplete="off" />
        </Form.Item>
        <Form.Item name="name" label="氏名" rules={[{ required: true, whitespace: true, message: "氏名を入力してください" }, { max: 100 }]}>
          <Input maxLength={100} />
        </Form.Item>
        <Form.Item name="email" label="メールアドレス" rules={[{ type: "email", message: "メールアドレスの形式で入力してください" }, { max: 255 }]}>
          <Input maxLength={255} autoComplete="off" />
        </Form.Item>
        <Form.Item
          name="roles"
          label="ロール"
          extra={locked.length ? "自分のシステム管理者ロールは外せません（管理者がいなくなるのを防ぐため）。" : "複数選べます。どのロールも全顧客に共通です。"}
        >
          <Checkbox.Group className="role-checks">
            {roles.map((r) => (
              <Checkbox key={r.code} value={r.code} disabled={locked.includes(r.code)}>
                <span className="role-check-name">{r.name}</span>
                {r.description && <span className="muted role-check-desc">{r.description}</span>}
              </Checkbox>
            ))}
          </Checkbox.Group>
        </Form.Item>
      </Form>
    </Modal>
  );
}
